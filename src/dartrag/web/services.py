"""웹 서버가 쓰는 의존성. 테스트에서는 가짜로 바꿔 끼운다.

임베딩 모델·검색 클라이언트는 무거워서 처음 쓸 때 한 번만 만들고,
DB 연결은 요청마다 새로 열어 요청끼리 트랜잭션이 섞이지 않게 한다.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from dartrag.config import DEFAULT_EXAMPLES, Settings
from dartrag.dashboard import DashboardCache
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
    # 가입한 이메일을 인증해야 이메일 알림을 켤 수 있다 (메일 발송이 설정된 경우에만 적용)
    email_verification_required: bool = False
    # 가입 없이 체험하기 (로그인을 켰을 때만): 체험 계정을 열지, 몇 시간 동안 쓰게 할지
    allow_guest: bool = False
    guest_hours: int = 24
    # 채팅 첫 화면의 예시 질문 (작업자가 답을 답변 캐시에 미리 넣어 둔다)
    examples: tuple[str, ...] = DEFAULT_EXAMPLES
    report_font: str | None = None  # PDF 리포트용 한글 TTF 경로 (없으면 자동 탐색)
    # 알림: 발송 수단({"email": …, "telegram": …}), 변경점 요약용 LLM
    senders: Callable[[], dict] = dict
    llm: Callable[[], object] | None = None
    public_url: str = "http://127.0.0.1:8000"
    secret_key: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""
    # 요청 한도: 범위(ask, heavy, auth, guest) → 규칙들. 비어 있으면 제한하지 않는다.
    # "guest:ask" 처럼 guest: 를 붙인 범위는 체험 계정에만 더하는 규칙 (접속 주소별로 센다)
    limits: dict = field(default_factory=dict)
    redis: Callable[[], object] | None = None
    # 운영 지표: /metrics 보호용 토큰, DB·Redis 상태 읽기
    metrics_token: str = ""
    ops_snapshot: Callable[[], dict] | None = None
    # LLM 추적(Langfuse). 탈퇴할 때 그 사용자의 추적 삭제를 요청하는 데 쓴다
    tracer: Callable[[], object] | None = None
    # 화면을 다른 주소에서 띄울 때(예: Next 개발 서버 http://localhost:3000) 그 주소
    allowed_origins: tuple[str, ...] = ()
    # 기업 대시보드 캐시. 기본은 이 프로세스 메모리, 배포 설정에서는 Redis (작업자가 미리 채운다)
    dashboards: DashboardCache = field(default_factory=DashboardCache)


def default_services(settings: Settings) -> Services:
    from functools import cache

    from dartrag.factory import (
        Backends,
        build_answerer,
        build_llm,
        build_retriever,
        build_senders,
    )
    from dartrag.obs.tracing import get_tracer

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
        email_verification_required=settings.email_verification_required,
        allow_guest=settings.allow_guest,
        guest_hours=settings.guest_hours,
        examples=settings.examples,
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
        tracer=lambda: get_tracer(settings),
        dashboards=DashboardCache(backends.redis, ttl=settings.dashboard_cache_ttl),
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
        # 체험 계정 만들기 (접속 주소별)
        "guest": [
            Rule(
                s.rate_guest_per_hour,
                3600,
                f"체험 계정 만들기는 1시간에 {s.rate_guest_per_hour}번",
            )
        ],
        # 체험 계정에만 더하는 한도 (회원 한도보다 낮게)
        "guest:ask": [
            Rule(
                s.rate_guest_ask_per_minute,
                60,
                f"체험 계정은 1분에 {s.rate_guest_ask_per_minute}번",
            ),
            Rule(s.rate_guest_ask_per_day, 86400, f"체험 계정은 하루 {s.rate_guest_ask_per_day}번"),
        ],
        "guest:heavy": [
            Rule(
                s.rate_guest_heavy_per_hour,
                3600,
                f"체험 계정은 1시간에 {s.rate_guest_heavy_per_hour}번",
            )
        ],
    }


def ops_snapshot(settings: Settings, repo, redis) -> dict:
    with repo() as r:
        snap = r.ops_snapshot(settings.index_focus)
    snap["dart_daily_limit"] = settings.dart_daily_limit
    if redis is not None:
        from dartrag.dart.quota import DailyQuota

        snap["dart_calls_today"] = DailyQuota(redis, settings.dart_daily_limit).used()
    return snap
