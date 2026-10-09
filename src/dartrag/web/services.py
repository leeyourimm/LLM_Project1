"""웹 서버가 쓰는 의존성. 테스트에서는 가짜로 바꿔 끼운다.

임베딩 모델·검색 클라이언트는 무거워서 처음 쓸 때 한 번만 만들고,
DB 연결은 요청마다 새로 열어 요청끼리 트랜잭션이 섞이지 않게 한다.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

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
        secret_key=settings.secret_key,
        telegram_bot_username=settings.telegram_bot_username,
        telegram_webhook_secret=settings.telegram_webhook_secret,
    )
