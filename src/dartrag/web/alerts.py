"""알림 설정 API: 사용자별 이메일·텔레그램 채널 등록, 인증, 구독 취소, 텔레그램 웹훅.

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

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, Path, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from dartrag.feed.alerts import check_unsubscribe
from dartrag.feed.telegram_bot import handle_update, new_link_code, token_hash

log = logging.getLogger(__name__)

Kind = Literal["email", "telegram"]
VERIFY_HOURS = 24
TELEGRAM_LINK_MINUTES = 30
RESEND_SECONDS = 60


class EnabledRequest(BaseModel):
    enabled: bool


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

    def throttle(user_id: int, kind: str) -> None:
        now = time.monotonic()
        if now - last_sent.get((user_id, kind), -1e9) < RESEND_SECONDS:
            raise HTTPException(429, "잠시 후 다시 시도해 주세요")
        last_sent[(user_id, kind)] = now

    @api.get("/api/alerts")
    def channels(user: CurrentUser = None):
        senders = services.senders()
        available = {
            "email": "email" in senders and bool(services.secret_key),
            "telegram": "telegram" in senders and bool(services.telegram_bot_username),
        }
        if user is None:
            return {"per_user": False, "available": available, "channels": []}
        with services.repo() as repo:
            rows = repo.alert_channels(user.id)
        return {"per_user": True, "available": available, "channels": rows}

    @api.post("/api/alerts/email", status_code=202)
    def start_email(background: BackgroundTasks, user: CurrentUser = None):
        """로그인한 이메일 주소로 인증 링크를 보낸다. 링크를 열어야 알림이 시작된다."""
        user = need_user(user)
        sender = services.senders().get("email")
        if sender is None:
            raise HTTPException(503, "메일 발송이 설정되지 않았습니다 (SMTP_HOST)")
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

    @api.patch("/api/alerts/{kind}")
    def set_enabled(kind: Annotated[Kind, Path()], req: EnabledRequest, user: CurrentUser = None):
        user = need_user(user)
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
