"""Postgres 통합 테스트. TEST_DATABASE_URL 이 없으면 건너뛴다."""

import os
from datetime import date

import psycopg
import pytest

from dartrag.dart.models import Corp, Filing
from dartrag.dart.reports import parse_report_name
from dartrag.db import Repository

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")


@pytest.fixture
def repo():
    conn = psycopg.connect(URL)
    conn.execute(
        "DROP TABLE IF EXISTS feedback, messages, conversations, sessions, user_watchlist, "
        "users, notifications, watchlist, disclosures, chunks, financial_items, filings, "
        "companies, app_state CASCADE"
    )
    conn.commit()
    r = Repository(conn)
    r.migrate()
    yield r
    conn.close()


def item(amount, **kw):
    return {
        "corp_code": "00126380",
        "bsns_year": 2024,
        "reprt_code": "11011",
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": "dart_OperatingIncomeLoss",
        "account_nm": "영업이익",
        "account_detail": None,
        "ord": 1,
        "amount": amount,
        "add_amount": None,
        "currency": "KRW",
        "raw_amount": str(amount),
        "raw_add_amount": None,
        "rcept_no": "20250311000001",
        **kw,
    }


def test_roundtrip(repo):
    for name in ("삼성전자", "삼성전자(주)"):
        repo.upsert_companies([Corp(corp_code="00126380", corp_name=name, stock_code="005930")])
    assert repo.conn.execute("SELECT corp_name FROM companies").fetchall() == [("삼성전자(주)",)]
    assert repo.fiscal_end_month("00126380") == 12

    filing = Filing(
        corp_code="00126380",
        corp_name="삼성전자",
        report_nm="사업보고서 (2024.12)",
        rcept_no="20250311000001",
        rcept_dt=date(2025, 3, 11),
    )
    report = parse_report_name(filing.report_nm)
    repo.upsert_filing(filing, report, "11011", "documents/2025/x.zip")
    repo.upsert_filing(filing, report, "11011", None)  # 재실행해도 원문 키 유지
    assert repo.conn.execute("SELECT raw_key FROM filings").fetchone() == ("documents/2025/x.zip",)

    # 정정공시로 다시 받으면 같은 보고서의 수치는 교체된다
    repo.replace_financials("00126380", 2024, "11011", "CFS", [item(100), item(5, ord=2)])
    repo.replace_financials("00126380", 2024, "11011", "CFS", [item(200)])
    rows = repo.conn.execute("SELECT amount FROM financial_items").fetchall()
    assert rows == [(200,)]


def test_chunks_replace_and_parse_state(repo):
    from dartrag.parsing import Chunk

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    filing = Filing(
        corp_code="00126380",
        corp_name="삼성전자",
        report_nm="사업보고서 (2024.12)",
        rcept_no="20250311000001",
        rcept_dt=date(2025, 3, 11),
    )
    repo.upsert_filing(filing, parse_report_name(filing.report_nm), "11011", "documents/x.zip")
    assert [r[0] for r in repo.filings_to_parse(1)] == ["20250311000001"]

    def chunk(i):
        return Chunk(f"c{i}", "text", ["I. 개요"], "ctx", f"body {i}", i)

    repo.replace_chunks("20250311000001", "00126380", [("a.xml", [chunk(0), chunk(1)])], 1)
    repo.replace_chunks("20250311000001", "00126380", [("a.xml", [chunk(2)])], 1)
    rows = repo.conn.execute("SELECT chunk_id, section_path FROM chunks").fetchall()
    assert rows == [("c2", ["I. 개요"])]
    assert repo.filings_to_parse(1) == []
    assert len(repo.filings_to_parse(2)) == 1  # 파서 버전이 오르면 다시 처리


def test_index_state_and_pipeline(repo):
    from qdrant_client import QdrantClient

    from dartrag.parsing import Chunk
    from dartrag.pipeline.index import INDEX_VERSION, index_filings
    from dartrag.search import SearchFilter, VectorIndex

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    assert repo.corp_codes_for_stocks(["005930", "000000"]) == ["00126380"]
    filing = Filing(
        corp_code="00126380",
        corp_name="삼성전자",
        report_nm="사업보고서 (2024.12)",
        rcept_no="20250311000001",
        rcept_dt=date(2025, 3, 11),
    )
    repo.upsert_filing(filing, parse_report_name(filing.report_nm), "11011", "documents/x.zip")
    assert repo.filings_to_index(INDEX_VERSION, "fake") == []  # 파싱 전에는 색인 대상 아님

    chunks = [Chunk(f"c{i}", "text", ["II. 사업"], "삼성전자 2024", f"매출 {i}", i) for i in (1, 2)]
    repo.replace_chunks("20250311000001", "00126380", [("a.xml", chunks)], 1)
    assert repo.filings_to_index(INDEX_VERSION, "fake") == ["20250311000001"]
    indexed = repo.indexed_chunks("20250311000001")
    assert [c.chunk_id for c in indexed] == ["c1", "c2"]
    assert indexed[0].text == "삼성전자 2024\n\n매출 1"
    assert indexed[0].period_year == 2024 and indexed[0].report_kind == "사업보고서"

    class Embedder:
        name, dim = "fake", 2

        def embed_documents(self, texts):
            return [[1.0, float(i)] for i, _ in enumerate(texts)]

        def embed_query(self, text):
            return [1.0, 0.0]

    class Keyword:
        def __init__(self):
            self.docs, self.deleted, self.refreshed = [], [], False

        def ensure(self):
            pass

        def delete_filing(self, rcept_no):
            self.deleted.append(rcept_no)

        def upsert(self, batch):
            self.docs += [c.chunk_id for c in batch]

        def refresh(self):
            self.refreshed = True

    vector, keyword = VectorIndex(QdrantClient(":memory:")), Keyword()
    summary = index_filings(repo, Embedder(), vector, keyword, batch_size=1)
    assert (summary.filings, summary.chunks, summary.errors) == (1, 2, [])
    assert keyword.docs == ["c1", "c2"] and keyword.refreshed
    assert sorted(vector.search([1.0, 0.0], SearchFilter(), 10)) == ["c1", "c2"]
    assert repo.filings_to_index(INDEX_VERSION, "fake") == []
    # 임베딩 모델이 바뀌면 다시 색인 대상
    assert repo.filings_to_index(INDEX_VERSION, "other") == ["20250311000001"]

    # 재파싱으로 청크가 바뀌면 다시 색인되고, 이전 청크는 인덱스에서 빠진다
    repo.replace_chunks("20250311000001", "00126380", [("a.xml", chunks[1:])], 1)
    index_filings(repo, Embedder(), vector, keyword)
    assert vector.search([1.0, 0.0], SearchFilter(), 10) == ["c2"]

    got = repo.get_chunks(["c2", "missing"])
    assert list(got) == ["c2"]
    assert got["c2"]["body"] == "매출 2"
    assert got["c2"]["url"].endswith("rcpNo=20250311000001")


def test_financial_rows_match_by_id_or_name(repo):
    repo.upsert_companies(
        [
            Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930"),
            Corp(corp_code="99999999", corp_name="비상장", stock_code=None),
        ]
    )
    assert repo.listed_companies() == [("00126380", "삼성전자")]
    assert repo.company_by_stock("005930") == ("00126380", "삼성전자", "005930")
    assert repo.company_by_stock("000000") is None
    repo.replace_financials(
        "00126380",
        2024,
        "11011",
        "CFS",
        [
            item(100, account_id="dart_OperatingIncomeLoss", account_nm="영업이익"),
            item(200, account_id="-표준계정코드 미사용-", account_nm="영업 이익", ord=2),
            item(300, account_id="x", account_nm="기타", ord=3),
            item(400, account_id="dart_OperatingIncomeLoss", sj_div="BS", ord=4),
        ],
    )
    rows = repo.financial_rows(
        ["00126380"], "11011", ("IS", "CIS"), ("dart_OperatingIncomeLoss",), ("영업이익",)
    )
    assert sorted(r.amount for r in rows) == [100, 200]
    assert {r.account_nm for r in rows} == {"영업이익"}
    assert rows[0].bsns_year == 2024 and rows[0].fs_div == "CFS"


def test_filing_pairs_for_diff(repo):
    from dartrag.parsing import Chunk

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    for rcept_no, name, dt in [
        ("20240312000001", "사업보고서 (2023.12)", date(2024, 3, 12)),
        ("20240501000001", "[기재정정]사업보고서 (2023.12)", date(2024, 5, 1)),
        ("20250311000001", "사업보고서 (2024.12)", date(2025, 3, 11)),
        ("20250515000001", "분기보고서 (2025.03)", date(2025, 5, 15)),
    ]:
        f = Filing(
            corp_code="00126380",
            corp_name="삼성전자",
            report_nm=name,
            rcept_no=rcept_no,
            rcept_dt=dt,
        )
        repo.upsert_filing(f, parse_report_name(name), "11011", "x.zip")
        repo.replace_chunks(
            rcept_no,
            "00126380",
            [(f"{rcept_no}.xml", [Chunk("c" + rcept_no, "text", ["I. 개요"], "ctx", "body", 0)])],
            1,
        )
    # 정정공시가 있으면 그 기간의 마지막 접수본
    assert repo.latest_filing_per_period("00126380", "사업보고서") == [
        "20240501000001",
        "20250311000001",
    ]
    info = repo.filing_info("20250311000001")
    assert info["corp_name"] == "삼성전자" and info["parsed"] is True
    assert repo.filing_info("00000000000000") is None
    assert repo.chunk_rows("20250311000001") == [("20250311000001.xml", 0, ["I. 개요"], "body")]


def test_disclosures_watchlist_and_alerts(repo):
    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])

    def d(no, importance, corp="00126380"):
        return {
            "rcept_no": f"2025031100000{no}",
            "corp_code": corp,
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "corp_cls": "Y",
            "report_nm": "보고서",
            "flr_nm": None,
            "rcept_dt": date(2025, 3, 11),
            "rm": None,
            "pblntf_ty": "B",
            "event_type": "x",
            "event_label": "라벨",
            "importance": importance,
            "correction": False,
        }

    # 관심 종목 등록 전에 본 공시는 알림하지 않는다
    assert repo.insert_disclosures([d(1, 3)]) == ["20250311000001"]
    repo.set_watch("00126380", 2)
    assert repo.pending_alerts("webhook") == []

    assert repo.insert_disclosures([d(1, 3), d(2, 3), d(3, 1), d(4, 3, corp="99999999")]) == [
        "20250311000002",
        "20250311000003",
        "20250311000004",
    ]
    assert [a["rcept_no"] for a in repo.pending_alerts("webhook")] == ["20250311000002"]
    repo.mark_notified("20250311000002", "webhook")
    assert repo.pending_alerts("webhook") == []
    assert len(repo.pending_alerts("console")) == 1  # 채널별로 따로 기록

    assert repo.watchlist() == [("00126380", "삼성전자", "005930", 2)]
    rows = repo.recent_disclosures(date(2025, 3, 1), 2, ["00126380"])
    assert [r["rcept_no"] for r in rows] == ["20250311000002", "20250311000001"]
    assert repo.remove_watch("00126380") and not repo.remove_watch("00126380")


def test_users_sessions_and_user_watchlist(repo):
    from datetime import UTC, datetime, timedelta

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    uid = repo.create_user("a@b.co", "scrypt$hash")
    assert uid and repo.create_user("a@b.co", "other") is None
    assert repo.user_by_email("a@b.co") == (uid, "a@b.co", "scrypt$hash")

    soon = datetime.now(UTC) + timedelta(days=1)
    repo.create_session("a" * 64, uid, soon)
    repo.create_session("b" * 64, uid, datetime.now(UTC) - timedelta(seconds=1))
    assert repo.session_user("a" * 64) == (uid, "a@b.co")
    assert repo.session_user("b" * 64) is None  # 만료
    assert repo.users()[0][3] is not None  # 마지막 로그인 시각

    repo.set_watch("00126380", 3, uid)
    assert repo.watchlist(uid) == [("00126380", "삼성전자", "005930", 3)]
    assert repo.watchlist() == []  # 운영자 목록과 분리
    assert repo.pending_alerts("webhook") == []
    assert repo.remove_watch("00126380", uid) and not repo.remove_watch("00126380", uid)

    repo.set_password(uid, "scrypt$new")
    assert repo.session_user("a" * 64) is None  # 비밀번호를 바꾸면 세션이 모두 끊김
    repo.create_session("c" * 64, uid, soon)
    repo.delete_session("c" * 64)
    assert repo.session_user("c" * 64) is None
    repo.create_session("d" * 64, uid, soon)
    repo.set_watch("00126380", 2, uid)
    assert repo.remove_user("a@b.co") and repo.users() == []
    assert repo.session_user("d" * 64) is None


def test_conversations_messages_feedback(repo):
    from dartrag.eval.feedback import feedback_to_cases

    uid = repo.create_user("a@b.co", "h")
    mine = repo.create_conversation(None, "삼성전자 2024년 영업이익은?")
    theirs = repo.create_conversation(uid, "다른 사람 대화")
    assert repo.owns_conversation(mine, None) and not repo.owns_conversation(mine, uid)
    assert not repo.owns_conversation(theirs, None)

    ctx = {"corp_codes": ["00126380"], "years": [2024], "topic": "영업이익"}
    q = repo.add_message(
        mine,
        "user",
        "그럼 전년은?",
        {"resolved_question": "삼성전자 2023년 영업이익?", "context": ctx},
    )
    a = repo.add_message(mine, "assistant", "6조 5,670억원입니다 [1].", {"sources": []})
    assert repo.last_context(mine) == ctx and repo.last_context(theirs) is None
    assert [c["id"] for c in repo.conversations(None)] == [mine]

    assert not repo.set_feedback(q, None, -1, None, None)  # 질문에는 평가 불가
    assert not repo.set_feedback(a, uid, -1, None, None)  # 남의 대화
    assert repo.set_feedback(a, None, 1, None, None)
    assert repo.set_feedback(a, None, -1, "wrong_number", "단위 틀림")  # 다시 누르면 바뀜
    msgs = repo.messages(mine)
    assert msgs[1]["rating"] == -1 and msgs[1]["reason"] == "wrong_number"

    rows = repo.feedback_rows(rating=-1)
    assert len(rows) == 1 and rows[0]["question"] == "그럼 전년은?"
    [case] = feedback_to_cases(rows)
    assert case.question == "삼성전자 2023년 영업이익?" and case.category == "numeric"
    assert case.corp_codes == ["00126380"] and "단위 틀림" in case.note

    assert repo.purge_conversations(30) == 0
    assert repo.delete_conversation(mine, None) and repo.feedback_rows() == []
    assert not repo.delete_conversation(theirs, None)


def test_expand_chunks_and_data_version(repo):
    from dartrag.parsing import Chunk

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    filing = Filing(
        corp_code="00126380",
        corp_name="삼성전자",
        report_nm="사업보고서 (2024.12)",
        rcept_no="20250311000001",
        rcept_dt=date(2025, 3, 11),
    )
    repo.upsert_filing(filing, parse_report_name(filing.report_nm), "11011", "documents/x.zip")
    chunks = [Chunk(f"c{i}", "text", ["II. 사업"], "ctx", f"문단{i}", i) for i in range(4)]
    chunks.append(Chunk("other", "text", ["III. 재무"], "ctx", "다른 섹션", 4))
    repo.replace_chunks("20250311000001", "00126380", [("a.xml", chunks)], 1)

    wide = repo.expand_chunks(["c1", "c3", "other"])
    assert wide["c1"] == "문단0\n문단1\n문단2"
    assert wide["c3"] == "문단2\n문단3"  # 다른 섹션은 붙이지 않음
    assert "other" not in wide  # 이웃이 없으면 넓히지 않음
    assert repo.expand_chunks(["c1"], max_chars=8) == {"c1": "문단0\n문단1"}

    assert repo.data_version() == 0
    repo.mark_indexed("20250311000001", 1, "m")
    repo.replace_financials("00126380", 2024, "11011", "CFS", [])
    assert repo.data_version() == 2
