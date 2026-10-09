"""브라우저 종단 테스트(frontend/e2e)용 백엔드.

실제 FastAPI 앱과 실제 Postgres(E2E_DATABASE_URL)를 쓰고, 검색과 언어 모델만 가짜로 바꾼다.
로그인을 켠 공개 서버 설정(AUTH_REQUIRED, 가입 허용)으로 띄운다.

    E2E_DATABASE_URL=postgresql://… python -m tests.e2e_server

탈퇴할 때 추적 삭제를 요청했는지 화면 테스트에서 확인할 수 있게 /api/_e2e/forgotten 을 연다.
이 파일은 테스트 전용이며 배포 코드에서 쓰지 않는다.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn

from dartrag.answer import Answerer
from dartrag.db import Repository
from dartrag.search import SearchHit
from dartrag.web.app import create_app
from dartrag.web.services import Services

ANSWER = ["삼성전자 2024년 DS 부문 매출은 ", "111조원입니다 [1]."]


class FakeRetriever:
    def search(self, query, flt=None, limit=10):
        chunk = {
            "corp_name": "삼성전자",
            "report_nm": "사업보고서 (2024.12)",
            "section_path": ["II. 사업의 내용", "1. 사업의 개요"],
            "kind": "text",
            "body": "DS 부문 매출은 111조원이다.",
            "unit": "억원",
            "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250311000001",
        }
        return [SearchHit("e2e-c1", 0.1, 1, None, chunk)][:limit]


class FakeLLM:
    name = "e2e-fake"

    def chat(self, messages):
        return "".join(ANSWER)

    def stream(self, messages):
        yield from ANSWER


class SpyTracer:
    """탈퇴 때 forget_user 가 불렸는지만 기록한다 (Langfuse 로 아무것도 보내지 않음)."""

    def __init__(self):
        self.forgotten: list[int] = []

    def forget_user(self, user_id) -> None:
        self.forgotten.append(user_id)


def build(url: str, origin: str):
    migrate = Repository.connect(url)
    try:
        migrate.migrate()
    finally:
        migrate.conn.close()

    @contextmanager
    def repo() -> Iterator[Repository]:
        r = Repository.connect(url)
        try:
            yield r
        finally:
            r.conn.close()

    spy = SpyTracer()
    services = Services(
        repo,
        lambda r: Answerer(FakeRetriever(), FakeLLM()),
        lambda r: FakeRetriever(),
        auth_required=True,
        allow_signup=True,
        cookie_secure=False,
        allowed_origins=(origin,),
        tracer=lambda: spy,
    )
    app = create_app(services)

    @app.get("/api/_e2e/forgotten", include_in_schema=False)
    def forgotten():
        return {"count": len(spy.forgotten)}

    return app


def main() -> None:
    url = os.environ["E2E_DATABASE_URL"]
    port = int(os.environ.get("E2E_API_PORT", "8765"))
    origin = os.environ.get("E2E_WEB_ORIGIN", "http://127.0.0.1:3100")
    uvicorn.run(build(url, origin), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
