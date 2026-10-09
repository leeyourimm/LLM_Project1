"""웹 API 와 화면.

화면은 빌드 도구 없이 바로 열 수 있게 정적 HTML·JS 한 벌로 만들었다 (static/).
"""

import pathlib
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dartrag.search import SearchFilter
from dartrag.web import auth
from dartrag.web.chat import DISCLAIMER, build_router
from dartrag.web.chat import source_dict as _source
from dartrag.web.insights import build_router as build_insights_router
from dartrag.web.services import Services

STATIC = pathlib.Path(__file__).parent / "static"


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=auth.PASSWORD_MAX)


class PasswordChange(BaseModel):
    current: str = Field(max_length=auth.PASSWORD_MAX)
    new: str = Field(max_length=auth.PASSWORD_MAX)


@dataclass(frozen=True)
class User:
    id: int
    email: str


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}
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
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "다른 사이트에서 온 요청은 받지 않습니다"}, 403)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = CSP
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

    def need_auth_mode():
        if not services.auth_required:
            raise HTTPException(400, "로그인을 쓰지 않는 설정입니다 (AUTH_REQUIRED=false)")

    @app.get("/api/auth/me")
    def me(request: Request):
        user = session_user(request) if services.auth_required else None
        return {
            "auth_required": services.auth_required,
            "allow_signup": services.allow_signup,
            "user": {"email": user.email} if user else None,
        }

    @app.post("/api/auth/signup", status_code=201)
    def signup(req: Credentials, response: Response):
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
        return {"user": {"email": email}}

    @app.post("/api/auth/login")
    def login(req: Credentials, request: Request, response: Response):
        need_auth_mode()
        ip = request.client.host if request.client else "-"
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
        limiter.succeeded(ip, email)
        start_session(response, row[0])
        return {"user": {"email": row[1]}}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(auth.SESSION_COOKIE)
        if token:
            with services.repo() as repo:
                repo.delete_session(auth.token_hash(token))
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"ok": True}

    @api.post("/api/auth/password")
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
            "disclaimer": DISCLAIMER,
        }

    api.include_router(build_router(services, CurrentUser, corp_codes))
    api.include_router(build_insights_router(services))
    app.include_router(api)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
