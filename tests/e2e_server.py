"""브라우저 종단 테스트(frontend/e2e)용 백엔드.

실제 FastAPI 앱과 실제 Postgres(E2E_DATABASE_URL)를 쓰고, 검색과 언어 모델만 가짜로 바꾼다.
답변 신뢰도의 최신 공시 비교를 확인할 수 있게 회사 하나와 정기공시 두 건을 넣어 둔다 (seed).
로그인을 켠 공개 서버 설정(AUTH_REQUIRED, 가입 허용)으로 띄운다.

    E2E_DATABASE_URL=postgresql://… python -m tests.e2e_server

탈퇴할 때 추적 삭제를 요청했는지 화면 테스트에서 확인할 수 있게 /api/_e2e/forgotten 을,
계정 메일(인증, 비밀번호 재설정)의 링크를 열어 볼 수 있게 /api/_e2e/mail 을 연다.
메일은 실제로 보내지 않고 메모리에만 담는다.
이 파일은 테스트 전용이며 배포 코드에서 쓰지 않는다.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import uvicorn

from dartrag.answer import Answerer
from dartrag.db import Repository
from dartrag.search import SearchHit
from dartrag.web.app import create_app
from dartrag.web.services import Services

ANSWER = ["삼성전자 2024년 DS 부문 매출은 ", "111조원입니다 [1]."]
RCEPT_NO = "20250311000001"
BODY = "DS 부문 매출은 111조원이다."
# 검색기가 붙이는 앞뒤 문단 (출처 패널이 흐리게 보여 주고 BODY 만 표시한다)
CONTEXT = (
    "당사는 DX(Device eXperience) 부문과 DS(Device Solutions) 부문으로 나누어 "
    "사업을 하고 있습니다.\n"
    f"{BODY}\n"
    "메모리 반도체 수요 회복으로 전년보다 매출이 늘었습니다."
)


class FakeRetriever:
    def search(self, query, flt=None, limit=10):
        chunk = {
            "rcept_no": RCEPT_NO,
            "rcept_dt": date(2025, 3, 11),
            "corp_name": "삼성전자",
            "report_nm": "사업보고서 (2024.12)",
            "section_path": ["II. 사업의 내용", "1. 사업의 개요"],
            "kind": "text",
            "body": BODY,
            "context_body": CONTEXT,
            "unit": "억원",
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RCEPT_NO}",
        }
        unused = chunk | {
            "section_path": ["II. 사업의 내용", "7. 기타 참고사항"],
            "body": "배당은 분기마다 지급한다.",
            "context_body": None,
        }
        hits = [
            SearchHit("e2e-c1", 0.1, 1, None, chunk),
            SearchHit("e2e-c2", 0.05, 2, None, unused),
        ]
        return hits[:limit]


def seed(repo: Repository) -> None:
    """답변 신뢰도의 최신 공시 비교용 회사와 정기공시 (인용한 사업보고서 뒤에 반기보고서가 있다)."""
    from dartrag.dart.models import Corp, Filing
    from dartrag.dart.reports import parse_report_name

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    for no, name, day in (
        (RCEPT_NO, "사업보고서 (2024.12)", date(2025, 3, 11)),
        ("20250814000002", "반기보고서 (2025.06)", date(2025, 8, 14)),
    ):
        filing = Filing(
            corp_code="00126380", corp_name="삼성전자", report_nm=name, rcept_no=no, rcept_dt=day
        )
        repo.upsert_filing(filing, parse_report_name(name), "11011", None)


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


class CaptureSender:
    """보낸 메일을 받는 주소별로 메모리에 담는다 (SMTP 로 보내지 않음)."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to, subject, text, unsubscribe_url=None) -> None:
        self.sent.append({"to": to, "subject": subject, "text": text})


def build(url: str, origin: str):
    migrate = Repository.connect(url)
    try:
        migrate.migrate()
        seed(migrate)
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
    mail = CaptureSender()
    services = Services(
        repo,
        lambda r: Answerer(FakeRetriever(), FakeLLM()),
        lambda r: FakeRetriever(),
        auth_required=True,
        allow_signup=True,
        cookie_secure=False,
        allowed_origins=(origin,),
        tracer=lambda: spy,
        senders=lambda: {"email": mail},
        public_url=origin,
    )
    app = create_app(services)

    @app.get("/api/_e2e/forgotten", include_in_schema=False)
    def forgotten():
        return {"count": len(spy.forgotten)}

    @app.get("/api/_e2e/mail", include_in_schema=False)
    def last_mail(to: str, subject: str = ""):
        found = [m for m in mail.sent if m["to"] == to and subject in m["subject"]]
        return found[-1] if found else {}

    return app


def main() -> None:
    url = os.environ["E2E_DATABASE_URL"]
    port = int(os.environ.get("E2E_API_PORT", "8765"))
    origin = os.environ.get("E2E_WEB_ORIGIN", "http://127.0.0.1:3100")
    uvicorn.run(build(url, origin), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
