"""웹 서버가 쓰는 의존성. 테스트에서는 가짜로 바꿔 끼운다.

임베딩 모델·검색 클라이언트는 무거워서 처음 쓸 때 한 번만 만들고,
DB 연결은 요청마다 새로 열어 요청끼리 트랜잭션이 섞이지 않게 한다.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from dartrag.config import Settings
from dartrag.db import Repository


@dataclass
class Services:
    repo: Callable[[], "contextmanager[Repository]"]
    answerer: Callable[[Repository], object]  # Answerer
    retriever: Callable[[Repository], object]  # HybridRetriever
    # 로그인: 내 컴퓨터에서만 쓸 때는 끄고(기본), 인터넷에 공개할 때 켠다
    auth_required: bool = False
    allow_signup: bool = True
    cookie_secure: bool = False
    session_days: int = 30
    report_font: str | None = None  # PDF 리포트용 한글 TTF 경로 (없으면 자동 탐색)
    # 알림: 발송 수단({"email": …, "telegram": …}), 변경점 요약용 LLM
    senders: Callable[[], dict] = dict
    llm: Callable[[], object] | None = None
    public_url: str = "http://127.0.0.1:8000"
    secret_key: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""
    # 요청 한도: 범위(ask, heavy, auth) → 규칙들. 비어 있으면 제한하지 않는다
    limits: dict = field(default_factory=dict)
    redis: Callable[[], object] | None = None
    # 운영 지표: /metrics 보호용 토큰, DB·Redis 상태 읽기
    metrics_token: str = ""
    ops_snapshot: Callable[[], dict] | None = None
    # 화면을 다른 주소에서 띄울 때(예: Next 개발 서버 http://localhost:3000) 그 주소
    allowed_origins: tuple[str, ...] = ()


def default_services(settings: Settings) -> Services:
    from functools import cache

    from dartrag.factory import (
        Backends,
        build_answerer,
        build_llm,
        build_retriever,
        build_senders,
    )

    backends = Backends(settings)

    @contextmanager
    def repo() -> Iterator[Repository]:
        r = Repository.connect(settings.database_url)
        try:
            yield r
        finally:
            r.conn.close()

    return Services(
        repo,
        lambda r: build_answerer(backends, r),
        lambda r: build_retriever(backends, r),
        auth_required=settings.auth_required,
        allow_signup=settings.allow_signup,
        cookie_secure=settings.cookie_secure,
        session_days=settings.session_days,
        report_font=settings.report_font,
        senders=cache(lambda: build_senders(settings)),
        llm=cache(lambda: build_llm(settings)),
        public_url=settings.public_url,
        allowed_origins=tuple(
            o.strip().rstrip("/") for o in settings.allowed_origins.split(",") if o.strip()
        ),
        secret_key=settings.secret_key,
        telegram_bot_username=settings.telegram_bot_username,
        telegram_webhook_secret=settings.telegram_webhook_secret,
        limits=rate_limits(settings),
        redis=lambda: backends.redis,
        metrics_token=settings.metrics_token,
        ops_snapshot=lambda: ops_snapshot(settings, repo, backends.redis),
    )


def rate_limits(settings: Settings) -> dict:
    from dartrag.web.ratelimit import Rule

    s = settings
    return {
        "ask": [
            Rule(s.rate_ask_per_minute, 60, f"1분에 {s.rate_ask_per_minute}번"),
            Rule(s.rate_ask_per_day, 86400, f"하루 {s.rate_ask_per_day}번"),
        ],
        "heavy": [Rule(s.rate_heavy_per_hour, 3600, f"1시간에 {s.rate_heavy_per_hour}번")],
        "auth": [Rule(s.rate_auth_per_hour, 3600, f"1시간에 {s.rate_auth_per_hour}번")],
    }


def ops_snapshot(settings: Settings, repo, redis) -> dict:
    with repo() as r:
        snap = r.ops_snapshot()
    snap["dart_daily_limit"] = settings.dart_daily_limit
    if redis is not None:
        from dartrag.dart.quota import DailyQuota

        snap["dart_calls_today"] = DailyQuota(redis, settings.dart_daily_limit).used()
    return snap
