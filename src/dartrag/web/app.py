"""웹 API 와 화면.

화면은 빌드 도구 없이 바로 열 수 있게 정적 HTML·JS 한 벌로 만들었다 (static/).
"""

import hmac
import logging
import pathlib
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from pydantic import BaseModel, Field

from dartrag.obs import metrics
from dartrag.obs.metrics import StateCollector
from dartrag.search import SearchFilter
from dartrag.web import account_mail, auth, two_factor
from dartrag.web.alerts import build_routers as build_alert_routers
from dartrag.web.chat import DISCLAIMER, build_router
from dartrag.web.chat import source_dict as _source
from dartrag.web.insights import build_router as build_insights_router
from dartrag.web.ratelimit import RateLimiter, Rule, make_dependency
from dartrag.web.services import Services

log = logging.getLogger(__name__)
STATIC = pathlib.Path(__file__).parent / "static"
log = logging.getLogger(__name__)

# 같은 주소로 재설정 메일이 쏟아지지 않게 주소별로도 센다 (넘어도 응답은 같고 메일만 안 보냄)
RESET_MAIL_RULE = Rule(3, 3600, "1시간에 3번")
VERIFY_RESEND_RULE = Rule(1, 60, "1분에 1번")
EXPIRED_RESET = "링크가 만료됐거나 이미 사용했습니다. 비밀번호 재설정을 다시 요청해 주세요"
EXPIRED_VERIFY = "링크가 만료됐거나 이미 사용했습니다. 계정 화면에서 인증 메일을 다시 받아 주세요"


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=auth.PASSWORD_MAX)


class PasswordChange(BaseModel):
    current: str = Field(max_length=auth.PASSWORD_MAX)
    new: str = Field(max_length=auth.PASSWORD_MAX)


class AccountDelete(BaseModel):
    password: str = Field(max_length=auth.PASSWORD_MAX)
    # 2단계 인증을 켰으면 인증 앱 코드나 복구 코드도 확인한다
    code: str = Field("", max_length=two_factor.CODE_MAX)


class ForgotRequest(BaseModel):
    email: str = Field(max_length=254)


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=100)


class PasswordReset(TokenRequest):
    password: str = Field(max_length=auth.PASSWORD_MAX)


class SecondFactor(BaseModel):
    code: str = Field(min_length=1, max_length=two_factor.CODE_MAX)


@dataclass(frozen=True)
class User:
    id: int
    email: str


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
# https 로 서비스할 때(COOKIE_SECURE=true)만. http 로 여는 내 컴퓨터에서는 붙이지 않는다
HSTS = "max-age=31536000"
# /api/docs 는 CDN 스크립트를 쓰므로 화면에만 적용
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class WatchRequest(BaseModel):
    stock: str = Field(pattern=r"^\d{6}$")
    min_importance: int = Field(2, ge=1, le=3)


def create_app(services: Services, limiter: auth.LoginLimiter | None = None) -> FastAPI:
    app = FastAPI(title="DART 공시 분석", docs_url="/api/docs", openapi_url="/api/openapi.json")
    limiter = limiter or auth.LoginLimiter()

    @app.middleware("http")
    async def security(request: Request, call_next):
        # 다른 사이트가 사용자 브라우저를 통해 몰래 보내는 요청(CSRF)을 막는다
        if request.method in UNSAFE_METHODS:
            origin = request.headers.get("origin")
            if origin:
                cross = (
                    urlsplit(origin).netloc != request.headers.get("host")
                    and origin.rstrip("/") not in services.allowed_origins
                )
            else:
                # Origin 을 빼고 보내는 브라우저도 Sec-Fetch-Site 는 붙인다. 메일 앱의 구독 취소,
                # 텔레그램 웹훅처럼 브라우저가 아닌 곳에서 오는 요청에는 둘 다 없다
                cross = request.headers.get("sec-fetch-site") == "cross-site"
            if cross:
                return JSONResponse({"detail": "다른 사이트에서 온 요청은 받지 않습니다"}, 403)
        started = time.perf_counter()
        response = await call_next(request)
        # 경로 대신 라우트 이름(/api/company/{stock})으로 세야 지표 종류가 끝없이 늘지 않는다
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "unmatched"
        if path != "/metrics":
            metrics.HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
            metrics.HTTP_SECONDS.labels(request.method, path).observe(time.perf_counter() - started)
        response.headers.update(SECURITY_HEADERS)
        if services.cookie_secure:
            response.headers["Strict-Transport-Security"] = HSTS
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = CSP
        # API 응답(대화, 관심 종목, 내 데이터)은 사용자별이라 브라우저·프록시에 남기지 않는다
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    def session_user(request: Request) -> User | None:
        token = request.cookies.get(auth.SESSION_COOKIE)
        if not token:
            return None
        with services.repo() as repo:
            row = repo.session_user(auth.token_hash(token))
        return User(*row) if row else None

    def current_user(request: Request) -> User | None:
        """로그인을 쓰지 않으면 None(운영자 본인), 쓰면 로그인한 사용자. 없으면 401."""
        if not services.auth_required:
            return None
        user = session_user(request)
        if user is None:
            raise HTTPException(401, "로그인이 필요합니다")
        return user

    CurrentUser = Annotated[User | None, Depends(current_user)]
    api = APIRouter(dependencies=[Depends(current_user)])

    def maybe_user(request: Request) -> User | None:
        """요청 한도용: 로그인하지 않았어도 오류 없이 None."""
        return session_user(request) if services.auth_required else None

    rate_limiter = RateLimiter(services.redis() if services.redis else None)

    def rate(scope: str, when=None):
        rules = services.limits.get(scope, [])
        return Depends(make_dependency(rate_limiter, scope, rules, maybe_user, when))

    def uid(user: User | None) -> int | None:
        return user.id if user else None

    def start_session(response: Response, user_id: int) -> None:
        token, hashed = auth.new_session_token()
        expires = datetime.now(UTC) + timedelta(days=services.session_days)
        with services.repo() as repo:
            repo.create_session(hashed, user_id, expires)
        response.set_cookie(
            auth.SESSION_COOKIE,
            token,
            max_age=services.session_days * 86400,
            httponly=True,
            samesite="lax",
            secure=services.cookie_secure,
            path="/",
        )

    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            auth.SESSION_COOKIE,
            path="/",
            httponly=True,
            samesite="lax",
            secure=services.cookie_secure,
        )

    def need_auth_mode():
        if not services.auth_required:
            raise HTTPException(400, "로그인을 쓰지 않는 설정입니다 (AUTH_REQUIRED=false)")

    # --- 2단계 인증: 비밀번호는 맞고 코드를 기다리는 로그인 ---------------------
    # 토큰은 HttpOnly 쿠키(5분, /api/auth 에만 보냄)에, DB 에는 해시만 둔다.
    # 화면 스크립트는 읽을 수 없다

    def start_challenge(response: Response, user_id: int) -> None:
        token, hashed = auth.new_session_token()
        until = datetime.now(UTC) + timedelta(minutes=two_factor.CHALLENGE_MINUTES)
        with services.repo() as repo:
            repo.create_login_challenge(hashed, user_id, until)
        response.set_cookie(
            two_factor.CHALLENGE_COOKIE,
            token,
            max_age=two_factor.CHALLENGE_MINUTES * 60,
            httponly=True,
            samesite="strict",
            secure=services.cookie_secure,
            path="/api/auth",
        )

    def challenge_failed(status: int, detail: str, *, restart: bool) -> JSONResponse:
        # HTTPException 으로는 쿠키를 지울 수 없어 응답을 직접 만든다
        res = JSONResponse({"detail": detail, "restart": restart}, status)
        if restart:
            res.delete_cookie(
                two_factor.CHALLENGE_COOKIE,
                path="/api/auth",
                httponly=True,
                samesite="strict",
                secure=services.cookie_secure,
            )
        return res

    # --- 계정 메일 (비밀번호 재설정, 이메일 인증, 새 기기 알림) -------------------

    def email_sender():
        """메일 발송 수단. SMTP 를 설정하지 않았으면 None (메일 기능은 조용히 꺼진다)."""
        return services.senders().get("email")

    def verification_enforced() -> bool:
        # 메일을 보낼 수 없으면 인증할 방법이 없으므로 강제하지 않는다 (doctor 가 알려 준다)
        return services.email_verification_required and email_sender() is not None

    def send_mail(background: BackgroundTasks, to: str, mail: tuple[str, str], what: str):
        """응답을 먼저 돌려주고 메일은 뒤에서 보낸다 (응답 시간으로 가입 여부가 드러나지 않게).
        받는 주소, 링크, SMTP 오류 내용은 로그에 남기지 않는다."""
        sender = email_sender()
        if sender is None:
            return

        def run():
            try:
                sender.send(to, *mail)
            except Exception as e:  # noqa: BLE001 - 메일이 실패해도 요청은 끝난다
                log.warning("%s 메일 발송 실패: %s", what, type(e).__name__)

        background.add_task(run)

    def send_verification(background: BackgroundTasks, user_id: int, email: str) -> None:
        token, hashed = auth.new_link_token()
        until = datetime.now(UTC) + timedelta(hours=account_mail.VERIFY_HOURS)
        with services.repo() as repo:
            repo.create_auth_token(user_id, "verify", hashed, until)
        mail = account_mail.verify_mail(services.public_url, token)
        send_mail(background, email, mail, "이메일 인증")

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "-"

    def note_login(
        request: Request,
        background: BackgroundTasks,
        user_id: int,
        email: str,
        notify: bool = True,
    ) -> None:
        """로그인한 기기를 기록하고, 처음 보는 기기면 인증된 주소로 알린다.

        기기 기록이 하나도 없던 계정(가입 직후, 이 기능 전에 만든 계정)은 알리지 않는다.
        인증하지 않은 주소에는 보내지 않는다 (남의 주소로 가입해 메일을 쏟아내지 못하게)."""
        ua = request.headers.get("user-agent")
        ip = client_ip(request)
        hashed = auth.device_hash(services.secret_key, user_id, ua, ip)
        with services.repo() as repo:
            status = repo.remember_device(user_id, hashed)
            alert = status == "new" and notify and repo.email_verified(user_id)
        if alert:
            browser, system = auth.device_label(ua)
            mail = account_mail.new_device_mail(
                services.public_url, browser, system, auth.network_of(ip)
            )
            send_mail(background, email, mail, "새 기기 로그인")

    @app.get("/api/auth/me")
    def me(request: Request):
        user = session_user(request) if services.auth_required else None
        info = None
        if user:
            with services.repo() as repo:
                info = {"email": user.email, "email_verified": repo.email_verified(user.id)}
        return {
            "auth_required": services.auth_required,
            "allow_signup": services.allow_signup,
            # 메일 발송이 설정됐는지 (비밀번호 재설정, 이메일 인증 화면이 쓴다)
            "email_enabled": services.auth_required and email_sender() is not None,
            "email_verification_required": services.auth_required and verification_enforced(),
            "user": info,
        }

    @app.post("/api/auth/signup", status_code=201, dependencies=[rate("auth")])
    def signup(req: Credentials, request: Request, response: Response, background: BackgroundTasks):
        need_auth_mode()
        if not services.allow_signup:
            raise HTTPException(403, "지금은 가입을 받지 않습니다. 관리자에게 계정을 요청하세요")
        try:
            email = auth.normalize_email(req.email)
            auth.check_password_policy(req.password)
        except auth.AuthError as e:
            raise HTTPException(422, str(e)) from None
        with services.repo() as repo:
            user_id = repo.create_user(email, auth.hash_password(req.password))
        if user_id is None:
            raise HTTPException(409, "이미 가입된 이메일입니다")
        start_session(response, user_id)
        note_login(request, background, user_id, email, notify=False)
        if email_sender() is not None:
            send_verification(background, user_id, email)
        return {"user": {"email": email, "email_verified": False}}

    @app.post("/api/auth/login")
    def login(req: Credentials, request: Request, response: Response, background: BackgroundTasks):
        need_auth_mode()
        ip = client_ip(request)
        email = (req.email or "").strip().lower()
        if limiter.blocked(ip, email):
            raise HTTPException(429, "로그인 시도가 너무 많습니다. 15분 뒤에 다시 해주세요")
        with services.repo() as repo:
            row = repo.user_by_email(email)
        # 없는 계정이어도 같은 계산을 해서 응답 시간으로 가입 여부를 알 수 없게 한다
        ok = auth.verify_password(req.password, row[2] if row else auth.DUMMY_HASH)
        if not (row and ok):
            limiter.failed(ip, email)
            raise HTTPException(401, "이메일 또는 비밀번호가 맞지 않습니다")
        with services.repo() as repo:
            needs_code = repo.totp_enabled(row[0])
        if needs_code:
            # 아직 로그인하지 않았다. 틀린 횟수도 코드까지 맞힌 뒤에 지운다
            start_challenge(response, row[0])
            return {"two_factor": True}
        limiter.succeeded(ip, email)
        start_session(response, row[0])
        note_login(request, background, row[0], row[1])
        return {"user": {"email": row[1]}}

    @app.post("/api/auth/login/2fa", dependencies=[rate("auth")])
    def login_second_factor(
        req: SecondFactor, request: Request, response: Response, background: BackgroundTasks
    ):
        """로그인 두 번째 단계: 인증 앱 코드나 복구 코드.

        틀리면 그 로그인 단계에서 5번까지, 계정 기준 로그인 시도 제한(LoginLimiter)에도
        함께 센다."""
        need_auth_mode()
        expired = "로그인 단계가 만료됐습니다. 이메일과 비밀번호부터 다시 입력해 주세요"
        token = request.cookies.get(two_factor.CHALLENGE_COOKIE)
        if not token:
            return challenge_failed(401, expired, restart=True)
        hashed = auth.token_hash(token)
        ip = client_ip(request)
        with services.repo() as repo:
            found = repo.login_challenge(hashed, two_factor.CHALLENGE_ATTEMPTS)
            if found is None:
                return challenge_failed(401, expired, restart=True)
            user_id, email = found
            if limiter.blocked(ip, email):
                raise HTTPException(429, "로그인 시도가 너무 많습니다. 15분 뒤에 다시 해주세요")
            result = two_factor.check_second_factor(services, repo, user_id, req.code)
            if result is None:
                limiter.failed(ip, email)
                left = repo.fail_login_challenge(hashed, two_factor.CHALLENGE_ATTEMPTS)
                if left == 0:
                    return challenge_failed(
                        401,
                        "인증 코드를 여러 번 틀렸습니다. 처음부터 다시 로그인해 주세요",
                        restart=True,
                    )
                return challenge_failed(401, two_factor.WRONG_CODE, restart=False)
            repo.delete_login_challenge(hashed)
        limiter.succeeded(ip, email)
        response.delete_cookie(
            two_factor.CHALLENGE_COOKIE,
            path="/api/auth",
            httponly=True,
            samesite="strict",
            secure=services.cookie_secure,
        )
        start_session(response, user_id)
        note_login(request, background, user_id, email)
        return {"user": {"email": email}} | result

    @app.post("/api/auth/password/forgot", status_code=202, dependencies=[rate("auth")])
    def forgot_password(req: ForgotRequest, background: BackgroundTasks):
        """재설정 링크 요청. 가입하지 않은 주소여도 같은 응답을 돌려준다."""
        need_auth_mode()
        if email_sender() is None:
            # 계정과 관계없는 서버 설정이라 알려 줘도 가입 여부는 드러나지 않는다
            raise HTTPException(503, account_mail.NO_MAIL)
        try:
            email = auth.normalize_email(req.email)
        except auth.AuthError as e:
            raise HTTPException(422, str(e)) from None
        # 주소는 해시해서 센다 (Redis 에 주소 원문을 남기지 않게)
        flooded = rate_limiter.hit("reset-mail", auth.token_hash(email), [RESET_MAIL_RULE])
        token = None
        with services.repo() as repo:
            row = repo.user_by_email(email)
            if row and not flooded:
                token, hashed = auth.new_link_token()
                until = datetime.now(UTC) + timedelta(minutes=account_mail.RESET_MINUTES)
                repo.create_auth_token(row[0], "reset", hashed, until)
        if token:
            mail = account_mail.reset_mail(services.public_url, token)
            send_mail(background, row[1], mail, "비밀번호 재설정")
        return {"ok": True, "message": account_mail.FORGOT_MESSAGE}

    @app.post("/api/auth/password/reset", dependencies=[rate("auth")])
    def reset_password(
        req: PasswordReset, request: Request, response: Response, background: BackgroundTasks
    ):
        """메일 링크의 토큰으로 새 비밀번호를 정한다. 다른 기기의 로그인은 모두 끊는다.

        2단계 인증은 그대로 둔다: 메일함만 빼앗은 사람이 재설정으로 2단계 인증을 건너뛰지 못하게,
        켠 사용자는 이 기기에서도 인증 앱 코드(또는 복구 코드)를 넣어야 로그인된다."""
        need_auth_mode()
        try:
            # 토큰을 쓰기 전에 검사해야 정책에 걸려도 같은 링크로 다시 할 수 있다
            auth.check_password_policy(req.password)
        except auth.AuthError as e:
            raise HTTPException(422, str(e)) from None
        with services.repo() as repo:
            found = repo.consume_auth_token("reset", auth.token_hash(req.token))
            if found is None:
                raise HTTPException(400, EXPIRED_RESET)
            user_id, email = found
            repo.reset_password(user_id, auth.hash_password(req.password))
            needs_code = repo.totp_enabled(user_id)
        if needs_code:
            start_challenge(response, user_id)
            return {"two_factor": True}
        limiter.succeeded(client_ip(request), email)
        start_session(response, user_id)
        # 메일을 받아 직접 바꾼 기기라 알리지 않고 기록만 한다
        note_login(request, background, user_id, email, notify=False)
        return {"user": {"email": email, "email_verified": True}}

    @app.post("/api/auth/verify-email", dependencies=[rate("auth")])
    def verify_email(req: TokenRequest):
        """가입 인증 링크. 로그인하지 않은 기기(휴대폰 메일 앱)에서 열어도 된다."""
        need_auth_mode()
        with services.repo() as repo:
            found = repo.consume_auth_token("verify", auth.token_hash(req.token))
            if found:
                repo.mark_email_verified(found[0])
        if found is None:
            raise HTTPException(400, EXPIRED_VERIFY)
        return {"ok": True}

    @api.post("/api/auth/verify-email/resend", status_code=202, dependencies=[rate("auth")])
    def resend_verification(user: CurrentUser, background: BackgroundTasks):
        need_auth_mode()
        if email_sender() is None:
            raise HTTPException(503, "메일 발송이 설정되지 않았습니다 (SMTP_HOST)")
        with services.repo() as repo:
            if repo.email_verified(user.id):
                return {"ok": True, "already_verified": True}
        if rate_limiter.hit("verify-mail", f"u{user.id}", [VERIFY_RESEND_RULE]):
            raise HTTPException(
                429, "인증 메일은 1분에 한 번 보낼 수 있습니다", headers={"Retry-After": "60"}
            )
        send_verification(background, user.id, user.email)
        return {"ok": True, "sent_to": user.email}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(auth.SESSION_COOKIE)
        if token:
            with services.repo() as repo:
                repo.delete_session(auth.token_hash(token))
        clear_session_cookie(response)
        return {"ok": True}

    @api.post("/api/auth/password", dependencies=[rate("auth")])
    def change_password(req: PasswordChange, user: CurrentUser, response: Response):
        need_auth_mode()
        with services.repo() as repo:
            row = repo.user_by_email(user.email)
            if not auth.verify_password(req.current, row[2]):
                raise HTTPException(401, "현재 비밀번호가 맞지 않습니다")
            try:
                auth.check_password_policy(req.new)
            except auth.AuthError as e:
                raise HTTPException(422, str(e)) from None
            repo.set_password(user.id, auth.hash_password(req.new))
        start_session(response, user.id)
        return {"ok": True}

    @api.delete("/api/account", dependencies=[rate("auth")])
    def delete_account(
        req: AccountDelete, user: CurrentUser, response: Response, background: BackgroundTasks
    ):
        """탈퇴: 지금 비밀번호를 확인하고 계정과 관심 종목, 알림 설정(웹 푸시 구독 포함),
        2단계 인증, 대화 기록을 모두 지운다. 2단계 인증을 켰으면 코드도 확인한다.

        LLM 추적(Langfuse)에 남은 그 사용자 가명의 기록도 응답을 보낸 뒤 지우도록 요청한다.
        """
        need_auth_mode()
        with services.repo() as repo:
            row = repo.user_by_email(user.email)
            if row is None or not auth.verify_password(req.password, row[2]):
                raise HTTPException(401, "비밀번호가 맞지 않습니다")
            if repo.totp_enabled(user.id) and (
                two_factor.check_second_factor(services, repo, user.id, req.code) is None
            ):
                raise HTTPException(401, two_factor.WRONG_CODE)
            repo.delete_user(user.id)
        if services.tracer is not None:
            background.add_task(forget_traces, user.id)
        clear_session_cookie(response)
        return {"ok": True}

    def forget_traces(user_id: int) -> None:
        try:
            services.tracer().forget_user(user_id)
        except Exception as e:  # noqa: BLE001 - 추적 삭제 실패로 탈퇴 응답이 깨지면 안 된다
            log.warning("탈퇴 후 추적 삭제 실패: %s", type(e).__name__)

    @api.get("/api/account/export", dependencies=[rate("heavy")])
    def export_account(user: CurrentUser):
        """내 데이터 내려받기 (JSON). 비밀번호 해시, 인증 코드, 2단계 인증 비밀값·복구 코드,
        웹 푸시 구독 주소·키는 넣지 않는다."""
        need_auth_mode()
        with services.repo() as repo:
            data = repo.export_user(user.id)
        if data is None:
            raise HTTPException(404, "계정을 찾을 수 없습니다")
        body = {"exported_at": datetime.now(UTC), "service": "DART 공시 분석"} | data
        filename = f"dartrag-export-{datetime.now(UTC):%Y%m%d}.json"
        return JSONResponse(
            jsonable_encoder(body),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    def corp_codes(repo, stocks: list[str]) -> list[str]:
        if not stocks:
            return []
        codes = repo.corp_codes_for_stocks(stocks)
        if not codes:
            raise HTTPException(404, "해당 종목코드의 기업이 없습니다")
        return codes

    @app.get("/api/health")
    def health():
        return {"ok": True}

    state = CollectorRegistry()
    if services.ops_snapshot is not None:
        state.register(StateCollector(services.ops_snapshot))

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint(request: Request):
        """Prometheus 수집용. 공개 서버에서는 프록시가 바깥 접근을 막는다."""
        if services.metrics_token:
            given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given, services.metrics_token):
                raise HTTPException(401, "metrics token required")
        body = generate_latest(metrics.REGISTRY) + generate_latest(state)
        return PlainTextResponse(body, media_type=CONTENT_TYPE_LATEST)

    @api.get("/api/companies")
    def companies():
        with services.repo() as repo:
            return [
                {"corp_code": c, "corp_name": n, "stock_code": s}
                for c, n, s in repo.listed_companies_with_stock()
            ]

    @api.get("/api/search")
    def search(
        q: Annotated[str, Query(min_length=2, max_length=500)],
        stocks: Annotated[list[str], Query()] = [],  # noqa: B006
        limit: Annotated[int, Query(ge=1, le=30)] = 10,
    ):
        with services.repo() as repo:
            flt = SearchFilter(corp_codes=corp_codes(repo, stocks))
            hits = services.retriever(repo).search(q, flt, limit)
        return [
            _source(i, h) | {"dense_rank": h.dense_rank, "keyword_rank": h.keyword_rank}
            for i, h in enumerate(hits, start=1)
        ]

    @api.get("/api/feed")
    def feed(
        days: Annotated[int, Query(ge=1, le=90)] = 7,
        min_importance: Annotated[int, Query(ge=1, le=3)] = 2,
        stocks: Annotated[list[str], Query()] = [],  # noqa: B006
        watched_only: bool = False,
        user: CurrentUser = None,
    ):
        with services.repo() as repo:
            codes = corp_codes(repo, stocks)
            if watched_only:
                codes = [c for c, *_ in repo.watchlist(uid(user))] or ["-"]
            rows = repo.recent_disclosures(
                date.today() - timedelta(days=days), min_importance, codes or None
            )
        return [
            r | {"url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}"}
            for r in rows
        ]

    @api.get("/api/watchlist")
    def watchlist(user: CurrentUser):
        with services.repo() as repo:
            return [
                {"corp_code": c, "corp_name": n, "stock_code": s, "min_importance": m}
                for c, n, s, m in repo.watchlist(uid(user))
            ]

    @api.post("/api/watchlist", status_code=201)
    def watch_add(req: WatchRequest, user: CurrentUser):
        with services.repo() as repo:
            [code] = corp_codes(repo, [req.stock])
            repo.set_watch(code, req.min_importance, uid(user))
        return {"ok": True}

    @api.delete("/api/watchlist/{stock}")
    def watch_remove(stock: str, user: CurrentUser):
        with services.repo() as repo:
            [code] = corp_codes(repo, [stock])
            if not repo.remove_watch(code, uid(user)):
                raise HTTPException(404, "관심 종목에 없습니다")
        return {"ok": True}

    @api.get("/api/diff")
    def diff(
        stock: Annotated[str, Query(pattern=r"^\d{6}$")],
        kind: str = "사업보고서",
    ):
        from dartrag.changes.compare import compare_filings, latest_pair

        with services.repo() as repo:
            [code] = corp_codes(repo, [stock])
            pair = latest_pair(repo, code, kind)
            if pair is None:
                raise HTTPException(404, f"비교할 {kind}가 두 건 이상 없습니다")
            try:
                result = compare_filings(repo, *pair)
            except ValueError as e:
                raise HTTPException(404, str(e)) from None
        return {
            "title": result.title,
            "old": result.old,
            "new": result.new,
            "sections": [
                {
                    "key": d.key,
                    "status": d.status,
                    "importance": d.importance,
                    "added": d.added,
                    "removed": d.removed,
                    "modified": [{"before": a, "after": b} for a, b in d.modified],
                    "numbers_only": len(d.numbers_only),
                }
                for d in result.diffs
            ],
        }

    @api.get("/api/company/{stock}")
    def company(
        stock: Annotated[str, Path(pattern=r"^\d{6}$")],
        years: Annotated[int, Query(ge=2, le=10)] = 5,
        days: Annotated[int, Query(ge=1, le=365)] = 90,
        user: CurrentUser = None,
    ):
        from dartrag.finance.series import company_series

        with services.repo() as repo:
            found = repo.company_by_stock(stock)
            if found is None:
                raise HTTPException(404, "해당 종목코드의 기업이 없습니다")
            code, name, stock_code = found
            series = company_series(repo, code, years)
            disclosures = repo.recent_disclosures(date.today() - timedelta(days=days), 1, [code])
            watched = any(c == code for c, *_ in repo.watchlist(uid(user)))
            issues = repo.data_issues(code)
        return {
            "corp_code": code,
            "corp_name": name,
            "stock_code": stock_code,
            "watched": watched,
            "series": [asdict(p) for p in series],
            "disclosures": [
                d | {"url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d['rcept_no']}"}
                for d in disclosures[:30]
            ],
            # 재무 데이터 검증에 걸린 항목: 화면에 "확인 필요"로 보여 준다
            "issues": [
                {
                    k: i[k]
                    for k in ("bsns_year", "reprt_code", "fs_div", "rule", "severity", "detail")
                }
                for i in issues
            ],
            "disclaimer": DISCLAIMER,
        }

    api.include_router(build_router(services, CurrentUser, corp_codes, rate))
    api.include_router(build_insights_router(services, rate))
    api.include_router(two_factor.build_router(services, CurrentUser, rate, start_session))
    alerts_api, alerts_public = build_alert_routers(services, CurrentUser)
    api.include_router(alerts_api)
    app.include_router(alerts_public)
    app.include_router(api)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
