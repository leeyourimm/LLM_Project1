"""알림 설정 API: 사용자별 이메일·텔레그램·웹 푸시 채널 등록, 인증, 구독 취소, 텔레그램 웹훅.

로그인을 켠 경우(AUTH_REQUIRED=true)에만 사용자별 채널을 쓴다.
로그인 없이 혼자 쓸 때는 .env 의 ALERT_* 설정으로 알림을 받는다.
"""

import hmac
import logging
import time
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from dartrag.feed import webpush
from dartrag.feed.alerts import check_unsubscribe, send_push
from dartrag.feed.channels import SendError
from dartrag.feed.telegram_bot import handle_update, new_link_code, token_hash
from dartrag.web.auth import device_label

log = logging.getLogger(__name__)

Kind = Literal["email", "telegram", "push"]
VERIFY_HOURS = 24
TELEGRAM_LINK_MINUTES = 30
RESEND_SECONDS = 60


class EnabledRequest(BaseModel):
    enabled: bool


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubscription(BaseModel):
    """브라우저 PushSubscription.toJSON() 모양. expirationTime 같은 다른 값은 쓰지 않는다."""

    endpoint: str = Field(max_length=webpush.MAX_ENDPOINT)
    keys: PushKeys


class PushEndpoint(BaseModel):
    endpoint: str = Field(max_length=webpush.MAX_ENDPOINT)


PUSH_TEST = {
    "title": "DART 공시 알림 시험",
    "body": "이 브라우저로 공시 알림이 옵니다.",
    "url": "/watchlist",
}


def _page(title: str, message: str, form: str = "") -> HTMLResponse:
    """form: 이미 이스케이프한 HTML 조각 (버튼 하나짜리 폼)."""
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title>"
        "<body style='font-family:system-ui,sans-serif;max-width:480px;margin:15vh auto;"
        "padding:0 16px;line-height:1.6'>"
        f"<h1 style='font-size:1.3rem'>{escape(title)}</h1><p>{escape(message)}</p>{form}"
        "<p><a href='/'>서비스로 돌아가기</a></p></body>"
    )


def build_routers(services, CurrentUser) -> tuple[APIRouter, APIRouter]:  # noqa: N803
    """(로그인이 필요한 API, 링크·웹훅처럼 로그인 없이 여는 API)."""
    api = APIRouter()
    public = APIRouter()
    last_sent: dict[tuple[int, str], float] = {}

    def need_user(user):
        if user is None:
            raise HTTPException(
                400,
                "사용자별 알림은 로그인을 켠 경우에만 씁니다. "
                "혼자 쓸 때는 .env 의 ALERT_ 설정을 쓰세요.",
            )
        return user

    def need_verified_email(user) -> None:
        """EMAIL_VERIFICATION_REQUIRED=true 면 가입한 이메일을 인증해야 이메일 알림을 켤 수 있다.
        메일 발송이 없으면 인증할 방법이 없어 강제하지 않는다."""
        if not services.email_verification_required or "email" not in services.senders():
            return
        with services.repo() as repo:
            verified = repo.email_verified(user.id)
        if not verified:
            raise HTTPException(
                403,
                "가입한 이메일을 먼저 인증해 주세요. "
                "계정 화면에서 인증 메일을 다시 받을 수 있습니다",
            )

    def throttle(user_id: int, kind: str) -> None:
        now = time.monotonic()
        if now - last_sent.get((user_id, kind), -1e9) < RESEND_SECONDS:
            raise HTTPException(429, "잠시 후 다시 시도해 주세요")
        last_sent[(user_id, kind)] = now

    def push_sender():
        sender = services.senders().get("push")
        if sender is None:
            raise HTTPException(
                503, "웹 푸시가 설정되지 않았습니다 (VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY)"
            )
        return sender

    @api.get("/api/alerts")
    def channels(user: CurrentUser = None):
        senders = services.senders()
        available = {
            "email": "email" in senders and bool(services.secret_key),
            "telegram": "telegram" in senders and bool(services.telegram_bot_username),
            "push": "push" in senders,
        }
        if user is None:
            return {"per_user": False, "available": available, "channels": []}
        with services.repo() as repo:
            rows = repo.alert_channels(user.id)
            # 이메일 알림을 켜려면 먼저 가입 이메일 인증이 필요한지 (화면 안내용)
            needs = (
                services.email_verification_required
                and available["email"]
                and not repo.email_verified(user.id)
            )
            devices = repo.push_devices(user.id) if available["push"] else []
        return {
            "per_user": True,
            "available": available,
            "channels": rows,
            "email_needs_verification": needs,
            # 브라우저가 구독할 때 쓰는 서버 공개키(applicationServerKey). 비밀키는 서버에만 있다
            "push_public_key": senders["push"].public_key if available["push"] else None,
            "push_devices": devices,
        }

    @api.post("/api/alerts/email", status_code=202)
    def start_email(background: BackgroundTasks, user: CurrentUser = None):
        """로그인한 이메일 주소로 인증 링크를 보낸다. 링크를 열어야 알림이 시작된다."""
        user = need_user(user)
        sender = services.senders().get("email")
        if sender is None:
            raise HTTPException(503, "메일 발송이 설정되지 않았습니다 (SMTP_HOST)")
        need_verified_email(user)
        throttle(user.id, "email")
        code, hashed = new_link_code()
        until = datetime.now(UTC) + timedelta(hours=VERIFY_HOURS)
        with services.repo() as repo:
            repo.start_alert_channel(user.id, "email", user.email, hashed, until)
        link = f"{services.public_url.rstrip('/')}/api/alerts/email/verify?token={code}"
        body = (
            "DART 공시 알림을 이메일로 받으려면 아래 링크를 여세요.\n"
            f"{link}\n\n{VERIFY_HOURS}시간 동안 유효합니다. 직접 요청하지 않았다면 무시하세요."
        )

        def send():
            try:
                sender.send(user.email, "[DART 알림] 이메일 알림 인증", body)
            except Exception as e:  # noqa: BLE001
                log.warning("인증 메일 실패: %s", e)

        background.add_task(send)
        return {"ok": True, "sent_to": user.email}

    @api.post("/api/alerts/telegram")
    def start_telegram(user: CurrentUser = None):
        """봇 연결 링크. 링크를 열고 '시작'을 누르면 그 대화방이 알림 채널이 된다."""
        user = need_user(user)
        if "telegram" not in services.senders() or not services.telegram_bot_username:
            raise HTTPException(503, "텔레그램 봇이 설정되지 않았습니다 (TELEGRAM_BOT_TOKEN)")
        code, hashed = new_link_code()
        until = datetime.now(UTC) + timedelta(minutes=TELEGRAM_LINK_MINUTES)
        with services.repo() as repo:
            repo.start_alert_channel(user.id, "telegram", None, hashed, until)
        bot = services.telegram_bot_username.lstrip("@")
        return {"link": f"https://t.me/{bot}?start={code}", "expires_at": until}

    # --- 웹 푸시 -----------------------------------------------------------

    @api.post("/api/alerts/push", status_code=201)
    def add_push(req: PushSubscription, request: Request, user: CurrentUser = None):
        """이 브라우저의 구독을 등록하고 웹 푸시 알림을 켠다.

        서버가 이 주소로 요청을 보내므로 알려진 푸시 서비스의 https 주소만 받는다."""
        user = need_user(user)
        sender = push_sender()
        try:
            webpush.check_endpoint(req.endpoint)
            webpush.check_subscription_keys(req.keys.p256dh, req.keys.auth)
        except webpush.PushKeyError as e:
            raise HTTPException(422, str(e)) from None
        browser, system = device_label(request.headers.get("user-agent"))
        with services.repo() as repo:
            sub_id = repo.add_push_subscription(
                user.id,
                req.endpoint,
                req.keys.p256dh.strip(),
                req.keys.auth.strip(),
                sender.public_key,
                f"{browser} · {system}",
            )
        return {"ok": True, "id": sub_id}

    @api.delete("/api/alerts/push/device")
    def remove_this_browser(req: PushEndpoint, user: CurrentUser = None):
        """이 브라우저 구독 해제 (화면이 브라우저 쪽 구독도 함께 지운다)."""
        user = need_user(user)
        with services.repo() as repo:
            removed = repo.remove_push_subscription(user.id, endpoint=req.endpoint)
        return {"ok": True, "removed": removed}

    @api.delete("/api/alerts/push/devices/{device_id}")
    def remove_device(device_id: int, user: CurrentUser = None):
        """목록에서 다른 브라우저 구독 해제."""
        user = need_user(user)
        with services.repo() as repo:
            if not repo.remove_push_subscription(user.id, sub_id=device_id):
                raise HTTPException(404, "등록된 브라우저가 아닙니다")
        return {"ok": True}

    @api.post("/api/alerts/push/test")
    def test_push(user: CurrentUser = None):
        """구독한 모든 브라우저로 시험 알림. 끝난 구독(404·410)은 이때도 지운다."""
        user = need_user(user)
        sender = push_sender()
        throttle(user.id, "push-test")
        with services.repo() as repo:
            if not repo.push_subscriptions(user.id):
                raise HTTPException(404, "알림을 받을 브라우저가 없습니다")
            try:
                sent = send_push(repo, sender, user.id, PUSH_TEST)
            except SendError as e:
                log.warning("사용자 %s 웹 푸시 시험 실패: %s", user.id, e)
                raise HTTPException(
                    502, "시험 알림을 보내지 못했습니다. 잠시 후 다시 해 보세요"
                ) from None
        return {"ok": True, "sent": sent}

    @api.patch("/api/alerts/{kind}")
    def set_enabled(kind: Annotated[Kind, Path()], req: EnabledRequest, user: CurrentUser = None):
        user = need_user(user)
        if kind == "email" and req.enabled:
            need_verified_email(user)
        with services.repo() as repo:
            if not repo.set_alert_enabled(user.id, kind, req.enabled):
                raise HTTPException(404, "등록된 알림 채널이 아닙니다")
        return {"ok": True}

    @api.delete("/api/alerts/{kind}")
    def remove(kind: Annotated[Kind, Path()], user: CurrentUser = None):
        user = need_user(user)
        with services.repo() as repo:
            if not repo.remove_alert_channel(user.id, kind):
                raise HTTPException(404, "등록된 알림 채널이 아닙니다")
        return {"ok": True}

    # --- 로그인 없이 여는 링크 ---------------------------------------------

    @public.get("/api/alerts/email/verify", include_in_schema=False)
    def verify_email(token: Annotated[str, Query(max_length=100)]):
        with services.repo() as repo:
            user_id = repo.confirm_alert_channel("email", token_hash(token))
            if user_id is not None:
                # 알림 인증 메일도 가입한 주소로만 보내므로 가입 이메일 인증으로 함께 친다
                repo.mark_email_verified(user_id)
        if user_id is None:
            return _page("링크가 만료됐습니다", "알림 설정에서 인증 메일을 다시 받아 주세요.")
        return RedirectResponse("/#alerts", status_code=303)

    def _valid(u: int, k: str, t: str) -> bool:
        return k in ("email", "telegram") and check_unsubscribe(services.secret_key, u, k, t)

    def _invalid() -> HTMLResponse:
        return _page("잘못된 링크입니다", "알림 설정 화면에서 직접 끌 수 있습니다.")

    def _unsubscribe(u: int, k: str, t: str) -> HTMLResponse:
        if not _valid(u, k, t):
            return _invalid()
        with services.repo() as repo:
            repo.set_alert_enabled(u, k, False)
        return _page("알림을 껐습니다", "다시 받으려면 알림 설정에서 켜 주세요.")

    # 메일 보안 검사기가 링크를 미리 열어도 꺼지지 않게, 링크를 열면 확인 버튼만 보여 준다
    @public.get("/api/alerts/unsubscribe", include_in_schema=False)
    def unsubscribe_page(u: int, k: str, t: Annotated[str, Query(max_length=128)]):
        if not _valid(u, k, t):
            return _invalid()
        action = escape(f"/api/alerts/unsubscribe?{urlencode({'u': u, 'k': k, 't': t})}")
        form = f"<form method='post' action='{action}'><button>알림 끄기</button></form>"
        return _page(
            "알림을 끌까요?", "아래 버튼을 누르면 이 채널로 오는 공시 알림이 멈춥니다.", form
        )

    # 메일 앱의 원클릭 구독 취소(RFC 8058)는 같은 주소로 POST 를 보낸다
    @public.post("/api/alerts/unsubscribe", include_in_schema=False)
    def unsubscribe_post(u: int, k: str, t: Annotated[str, Query(max_length=128)]):
        return _unsubscribe(u, k, t)

    @public.post("/api/telegram/webhook", include_in_schema=False)
    def telegram_webhook(
        update: Annotated[dict, Body()],
        token: Annotated[str, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = "",
    ):
        # 텔레그램이 보낸 요청인지 등록할 때 정한 비밀값으로 확인한다
        secret = services.telegram_webhook_secret
        if not secret or not hmac.compare_digest(secret, token):
            raise HTTPException(403, "forbidden")
        sender = services.senders().get("telegram")
        if sender is not None:
            with services.repo() as repo:
                handle_update(repo, update, sender)
        return {"ok": True}

    return api, public
