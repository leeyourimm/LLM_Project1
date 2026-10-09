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
        "DROP TABLE IF EXISTS company_versions, eval_runs, data_issues, job_runs, backfill_state, "
        "diff_summaries, user_notifications, user_push_subscriptions, user_alert_channels, "
        "auth_tokens, user_devices, login_challenges, user_recovery_codes, user_totp, "
        "feedback, messages, conversations, sessions, user_watchlist, "
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
    from dartrag.pipeline.index import INDEX_VERSION, index_filings, rebuild_keyword_index
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
    assert not repo.has_indexed_filings()  # 파싱만 하고 색인 전이면 답할 공시가 없다
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
    assert repo.has_indexed_filings()
    assert repo.indexed_filing_numbers() == ["20250311000001"]
    # 다른 서버로 옮긴 뒤: 임베딩 없이 DB 의 청크로 키워드 색인만 다시 채운다
    moved = Keyword()
    rebuilt = rebuild_keyword_index(repo, moved)
    assert (rebuilt.filings, rebuilt.chunks, rebuilt.errors) == (1, 2, [])
    assert moved.docs == ["c1", "c2"] and moved.deleted == ["20250311000001"] and moved.refreshed
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


def test_quarter_rows_include_report_code_and_cumulative(repo):
    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    repo.replace_financials("00126380", 2024, "11011", "CFS", [item(400)])
    repo.replace_financials(
        "00126380",
        2024,
        "11014",
        "CFS",
        [item(90, reprt_code="11014", add_amount=300, rcept_no="20241114000001")],
    )
    rows = repo.quarter_rows(
        "00126380", ("IS", "CIS"), ("dart_OperatingIncomeLoss",), ("영업이익",)
    )
    got = sorted((r.reprt_code, r.amount, r.add_amount) for r in rows)
    assert got == [("11011", 400, None), ("11014", 90, 300)]


def test_user_alert_channels_and_pending(repo):
    from datetime import UTC, datetime, timedelta

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    uid = repo.create_user("a@b.co", "h")
    other = repo.create_user("c@d.co", "h")
    repo.set_watch("00126380", 2, uid)
    repo.set_watch("00126380", 3, other)
    later = datetime.now(UTC) + timedelta(hours=1)

    repo.start_alert_channel(uid, "email", "a@b.co", "e" * 64, later)
    repo.start_alert_channel(uid, "telegram", None, "t" * 64, later)
    repo.start_alert_channel(other, "telegram", None, "x" * 64, datetime.now(UTC))  # 만료
    assert repo.confirm_alert_channel("telegram", "x" * 64, target="9") is None
    assert repo.confirm_alert_channel("email", "e" * 64) == uid
    assert repo.confirm_alert_channel("email", "e" * 64) is None  # 한 번만 쓴다
    assert repo.confirm_alert_channel("telegram", "t" * 64, target="42") == uid
    chans = {c["kind"]: c for c in repo.alert_channels(uid)}
    assert chans["email"]["verified"] and chans["telegram"]["target"] == "42"

    base = {
        "corp_code": "00126380",
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
        "correction": False,
    }
    repo.insert_disclosures(
        [
            {**base, "rcept_no": "20250311000001", "importance": 3},
            {**base, "rcept_no": "20250311000002", "importance": 1},
        ]
    )
    pending = repo.user_pending_alerts()
    assert [(p["user_id"], p["kind"], p["rcept_no"]) for p in pending] == [
        (uid, "email", "20250311000001"),
        (uid, "telegram", "20250311000001"),
    ]  # 인증 안 된 다른 사용자, 중요도 낮은 공시는 빠진다
    repo.mark_user_notified(uid, ["20250311000001"], "email")
    assert [p["kind"] for p in repo.user_pending_alerts()] == ["telegram"]
    assert repo.disable_telegram_chat("42") == 1
    assert repo.user_pending_alerts() == []
    assert repo.set_alert_enabled(uid, "telegram", True)
    assert repo.remove_alert_channel(uid, "telegram") and not repo.remove_alert_channel(
        uid, "telegram"
    )
    assert repo.user_pending_alerts() == []


def test_diff_summary_storage(repo):
    assert repo.diff_summary("a" * 14, "b" * 14) is None
    payload = {"old": {"rcept_dt": date(2024, 3, 12)}, "points": {}}
    repo.save_diff_summary("a" * 14, "b" * 14, 1, "qwen3:8b", payload)
    repo.save_diff_summary("a" * 14, "b" * 14, 1, None, payload)
    saved = repo.diff_summary("a" * 14, "b" * 14)
    assert saved["model"] is None and saved["payload"]["old"]["rcept_dt"] == "2024-03-12"
    assert repo.latest_diff_summary_for("b" * 14) == saved["payload"]


def feed_row(no, pblntf_ty="A", importance=2, corp="00126380"):
    return {
        "rcept_no": no,
        "corp_code": corp,
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "corp_cls": "Y",
        "report_nm": "사업보고서 (2025.12)",
        "flr_nm": None,
        "rcept_dt": date(2026, 3, 10),
        "rm": None,
        "pblntf_ty": pblntf_ty,
        "event_type": "periodic_report",
        "event_label": "정기보고서",
        "importance": importance,
        "correction": False,
    }


def test_ingest_queue_and_alert_hold(repo):
    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    repo.set_watch("00126380", 2)
    repo.insert_disclosures(
        [
            feed_row("20260310000001"),
            feed_row("20260310000002", "B", 3),
            feed_row("20260310000003", corp="99999999"),  # 우리 DB 에 없는 회사
        ]
    )
    assert [d["rcept_no"] for d in repo.periodic_to_ingest()] == ["20260310000001"]
    # 정기보고서 알림은 처리가 끝날 때까지 기다린다
    assert [a["rcept_no"] for a in repo.pending_alerts("webhook")] == ["20260310000002"]
    for _ in range(5):
        repo.mark_ingest_failed("20260310000001", "boom")
    assert repo.periodic_to_ingest() == []
    assert repo.ingest_backlog()["failed"] == 1
    # 5번 실패하면 더 기다리지 않고 알린다
    assert len(repo.pending_alerts("webhook")) == 2
    repo.conn.execute(
        "UPDATE disclosures SET ingest_attempts = 0, ingest_error = NULL"
        " WHERE rcept_no = '20260310000001'"
    )
    repo.mark_ingested("20260310000001")
    assert repo.ingest_backlog()["pending"] == 0
    assert len(repo.pending_alerts("webhook")) == 2


def test_backfill_jobs_and_issues(repo):
    from dartrag.finance.validate import Issue

    repo.upsert_companies(
        [
            Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930"),
            Corp(corp_code="00164779", corp_name="SK하이닉스", stock_code="000660"),
            Corp(corp_code="99999999", corp_name="비상장", stock_code=None),
        ]
    )
    assert repo.plan_backfill(2015, 2026) == 2
    assert repo.plan_backfill(2015, 2026) == 0
    assert repo.next_backfill(10) == [("00126380", 2015, 2026), ("00164779", 2015, 2026)]
    repo.finish_backfill("00126380", 40)
    repo.fail_backfill("00164779", "boom")
    assert repo.backfill_progress() == {"pending": 0, "done": 1, "error": 1}
    assert repo.next_backfill(10) == [("00164779", 2015, 2026)]
    repo.plan_backfill(2010, 2026)  # 기간을 넓히면 다시 대기
    assert repo.backfill_progress()["pending"] == 2

    jid = repo.start_job("ingest")
    repo.finish_job(jid, "ok", {"when": date(2026, 3, 10)})
    repo.finish_job(repo.start_job("ingest"), "error", {"error": "x"})
    last = repo.last_job_runs()["ingest"]
    assert last["last_status"] == "error" and last["last_ok"] is not None
    assert repo.recent_jobs(1)[0]["detail"] == {"error": "x"}
    assert repo.purge_job_runs(1) == 0

    a = Issue("00126380", 2024, "11011", "CFS", "balance", "error", "어긋남")
    b = Issue("00126380", 2024, "11011", "CFS", "jump", "warn", "급변")
    repo.replace_issues("00126380", [a, b])
    first = {i["rule"]: i["first_seen"] for i in repo.data_issues("00126380")}
    repo.replace_issues("00126380", [a])
    again = repo.data_issues("00126380")
    assert [i["rule"] for i in again] == ["balance"] and again[0]["first_seen"] == first["balance"]
    assert again[0]["corp_name"] == "삼성전자"
    assert repo.data_issues(severity="warn") == []
    repo.replace_issues("00126380", [])
    assert repo.data_issues() == []
    assert repo.purge_expired_sessions() == 0 and repo.purge_user_notifications(90) == 0


def test_ops_snapshot_and_eval_runs(repo):
    from dartrag.finance.validate import Issue

    repo.finish_job(repo.start_job("feed_poll"), "ok", {})
    repo.replace_issues("00126380", [Issue("00126380", 2024, "11011", "CFS", "jump", "warn", "x")])
    snap = repo.ops_snapshot()
    assert snap["jobs"][0]["name"] == "feed_poll" and snap["jobs"][0]["status"] == "ok"
    assert snap["issues"] == {"error": 0, "warn": 1}
    assert snap["ingest_backlog"] == 0 and snap["users"] == 0 and "eval" not in snap

    repo.save_eval_run({"llm": "a"}, {"overall": {"pass_rate": 0.7}}, False)
    repo.save_eval_run({"llm": "b"}, {"overall": {"pass_rate": 0.9}}, True)
    latest = repo.ops_snapshot()["eval"]
    assert latest["meta"] == {"llm": "b"} and latest["passed"] is True


def _user_references(repo) -> list[tuple[str, str]]:
    """users(id) 를 가리키는 모든 (표, 열). 나중에 표가 늘어도 이 목록으로 검사한다."""
    return repo.conn.execute(
        """SELECT cl.relname, a.attname
           FROM pg_constraint c
           JOIN pg_class cl ON cl.oid = c.conrelid
           JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
           WHERE c.contype = 'f' AND c.confrelid = 'users'::regclass
           ORDER BY 1"""
    ).fetchall()


def _seed_user(repo, email: str, rcept_no: str) -> dict:
    from datetime import UTC, datetime, timedelta

    uid = repo.create_user(email, "scrypt$secret-hash")
    later = datetime.now(UTC) + timedelta(days=1)
    repo.create_session(email[0] * 64, uid, later)
    repo.set_watch("00126380", 2, uid)
    repo.start_alert_channel(uid, "email", email, "p" * 63 + email[0], later)
    repo.start_alert_channel(uid, "telegram", None, "q" * 63 + email[0], later)
    repo.confirm_alert_channel("telegram", "q" * 63 + email[0], target="42")
    repo.mark_user_notified(uid, [rcept_no], "email")
    repo.create_auth_token(uid, "reset", "r" * 63 + email[0], later)
    repo.create_auth_token(uid, "verify", "v" * 63 + email[0], later)
    assert repo.remember_device(uid, "h" * 63 + email[0]) == "first"
    repo.add_push_subscription(
        uid,
        f"https://fcm.googleapis.com/fcm/send/push-secret-{email}",
        "p256",
        "auth",
        "vapid",
        "Chrome · Windows",
    )
    assert repo.start_totp(uid, f"sealed-totp-{email}")
    assert repo.enable_totp(uid, 1, ["k" * 63 + email[0], "K" * 63 + email[0]])
    repo.create_login_challenge("c" * 63 + email[0], uid, later)
    repo.create_session(email[0] * 64, uid, later)  # 2단계 인증을 켜면 세션이 끊기므로 다시
    conv = repo.create_conversation(uid, f"{email} 질문")
    repo.add_message(conv, "user", "삼성전자 매출은?", {"context": {}})
    answer = repo.add_message(conv, "assistant", "답 [1]", {"sources": []})
    assert repo.set_feedback(answer, uid, -1, "wrong_number", "숫자가 달라요")
    return {"uid": uid, "conv": conv, "answer": answer}


def _count(repo, table: str, column: str | None = None, value=None) -> int:
    where = f" WHERE {column} = %s" if column else ""
    params = (value,) if column else ()
    return repo.conn.execute(f"SELECT count(*) FROM {table}{where}", params).fetchone()[0]


def test_delete_user_leaves_no_rows(repo):
    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    repo.conn.execute(
        """INSERT INTO disclosures (rcept_no, corp_code, corp_name, report_nm, rcept_dt,
               pblntf_ty, event_type, event_label, importance, correction)
           VALUES ('20250311000001', '00126380', '삼성전자', 'x', '2025-03-11',
                   'B', 'x', 'x', 3, false)"""
    )
    repo.conn.commit()
    a = _seed_user(repo, "a@b.co", "20250311000001")
    b = _seed_user(repo, "c@d.co", "20250311000001")

    refs = _user_references(repo)
    assert {t for t, _ in refs} >= {
        "sessions",
        "user_watchlist",
        "user_alert_channels",
        "user_notifications",
        "conversations",
        "auth_tokens",
        "user_devices",
        "user_push_subscriptions",
        "user_totp",
        "user_recovery_codes",
        "login_challenges",
    }
    # 사용자를 가리키는 외래 키는 모두 ON DELETE CASCADE (직접 지우기를 빠뜨려도 남지 않게)
    rules = repo.conn.execute(
        "SELECT conrelid::regclass::text, confdeltype FROM pg_constraint "
        "WHERE contype = 'f' AND confrelid = 'users'::regclass"
    ).fetchall()
    assert rules and all(rule == "c" for _, rule in rules), rules

    exported = repo.export_user(a["uid"])
    assert exported["account"]["email"] == "a@b.co"
    assert [w["stock_code"] for w in exported["watchlist"]] == ["005930"]
    assert {c["kind"]: c["target"] for c in exported["alert_channels"]} == {
        "email": "a@b.co",
        "telegram": "42",
        "push": None,
    }
    assert exported["notifications"][0]["rcept_no"] == "20250311000001"
    assert len(exported["devices"]) == 1 and exported["account"]["email_verified_at"] is None
    # 웹 푸시는 푸시 서비스 호스트만, 2단계 인증은 켰는지와 남은 복구 코드 수만
    [push] = exported["push_subscriptions"]
    assert push["push_service"] == "fcm.googleapis.com" and push["label"] == "Chrome · Windows"
    assert exported["two_factor"]["enabled"] is True
    assert exported["two_factor"]["recovery_codes_left"] == 2
    [conv] = exported["conversations"]
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["feedback"]["comment"] == "숫자가 달라요"
    assert conv["messages"][0]["feedback"] is None
    flat = repr(exported)
    assert "secret-hash" not in flat and "pppp" not in flat and "c@d.co" not in flat
    assert "rrrr" not in flat and "vvvv" not in flat and "hhhh" not in flat
    assert "push-secret" not in flat and "p256" not in flat and "sealed-totp" not in flat
    assert "kkkk" not in flat and "cccc" not in flat

    assert repo.delete_user(a["uid"]) and not repo.delete_user(a["uid"])
    assert repo.export_user(a["uid"]) is None
    for table, column in refs:
        assert _count(repo, table, column, a["uid"]) == 0, table
    assert _count(repo, "messages", "conversation_id", a["conv"]) == 0
    assert _count(repo, "feedback", "message_id", a["answer"]) == 0
    assert _count(repo, "users", "id", a["uid"]) == 0
    # 다른 사용자의 기록과 공시는 그대로
    assert repo.export_user(b["uid"])["conversations"][0]["messages"][1]["feedback"]
    assert repo.session_user("c" * 64) == (b["uid"], "c@d.co")
    assert _count(repo, "disclosures") == 1

    # CLI(dartrag user remove)도 같은 삭제 경로를 쓴다
    assert repo.remove_user("c@d.co") and not repo.remove_user("c@d.co")
    for table, _ in refs:
        assert _count(repo, table) == 0, table
    assert _count(repo, "messages") == 0 and _count(repo, "feedback") == 0


def test_auth_tokens_devices_and_email_verification(repo):
    from datetime import UTC, datetime, timedelta

    uid = repo.create_user("a@b.co", "scrypt$old")
    later = datetime.now(UTC) + timedelta(minutes=30)
    assert repo.email_verified(uid) is False

    # 한 번만 쓰고, 용도가 다르면 쓸 수 없다
    repo.create_auth_token(uid, "verify", "v" * 64, later)
    assert repo.consume_auth_token("reset", "v" * 64) is None
    assert repo.consume_auth_token("verify", "v" * 64) == (uid, "a@b.co")
    assert repo.consume_auth_token("verify", "v" * 64) is None
    repo.mark_email_verified(uid)
    assert repo.email_verified(uid) is True

    # 새 링크를 만들면 예전 링크는 지워진다. 만료된 링크는 쓸 수 없고 지워진다
    repo.create_auth_token(uid, "reset", "1" * 64, later)
    repo.create_auth_token(uid, "reset", "2" * 64, later)
    assert repo.consume_auth_token("reset", "1" * 64) is None
    repo.conn.execute(
        "UPDATE auth_tokens SET expires_at = now() - interval '1 second' WHERE token_hash = %s",
        ("2" * 64,),
    )
    repo.conn.commit()
    assert repo.consume_auth_token("reset", "2" * 64) is None
    assert _count(repo, "auth_tokens") == 0

    # 재설정: 비밀번호를 바꾸고 모든 세션과 남은 재설정 링크를 지운다
    repo.create_session("s" * 64, uid, later)
    repo.create_auth_token(uid, "reset", "3" * 64, later)
    repo.create_auth_token(uid, "verify", "4" * 64, later)
    assert repo.consume_auth_token("reset", "3" * 64) == (uid, "a@b.co")
    repo.create_auth_token(uid, "reset", "5" * 64, later)
    repo.reset_password(uid, "scrypt$new")
    assert repo.user_by_email("a@b.co")[2] == "scrypt$new"
    assert repo.session_user("s" * 64) is None
    assert repo.consume_auth_token("reset", "5" * 64) is None
    assert repo.consume_auth_token("verify", "4" * 64) == (uid, "a@b.co")

    # 재설정 메일을 받은 사용자는 이메일도 인증된 것으로 본다
    other = repo.create_user("c@d.co", "scrypt$x")
    repo.reset_password(other, "scrypt$y")
    assert repo.email_verified(other) is True

    # 기기 기록
    assert repo.remember_device(uid, "d" * 64) == "first"
    assert repo.remember_device(uid, "d" * 64) == "known"
    assert repo.remember_device(uid, "e" * 64) == "new"
    assert repo.remember_device(uid, "e" * 64) == "known"
    # 1년 동안 쓰지 않은 기기는 잊는다
    repo.conn.execute(
        "UPDATE user_devices SET last_seen = now() - interval '400 days' WHERE device_hash = %s",
        ("e" * 64,),
    )
    repo.conn.commit()
    assert repo.remember_device(uid, "e" * 64) == "new"


def test_migration_marks_alert_verified_users(repo):
    from datetime import UTC, datetime, timedelta

    uid = repo.create_user("a@b.co", "scrypt$x")
    later = datetime.now(UTC) + timedelta(days=1)
    repo.start_alert_channel(uid, "email", "a@b.co", "p" * 64, later)
    repo.confirm_alert_channel("email", "p" * 64)
    assert repo.email_verified(uid) is False
    repo.migrate()  # 마이그레이션은 여러 번 돌려도 된다
    assert repo.email_verified(uid) is True


def _disclosure(no: str, corp: str = "00126380", importance: int = 3) -> dict:
    return {
        "rcept_no": no,
        "corp_code": corp,
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "corp_cls": "Y",
        "report_nm": "주요사항보고서(유상증자결정)",
        "flr_nm": None,
        "rcept_dt": date(2025, 3, 11),
        "rm": None,
        "pblntf_ty": "B",
        "event_type": "x",
        "event_label": "유상증자",
        "importance": importance,
        "correction": False,
    }


def test_push_subscriptions_and_pending(repo):
    import hashlib

    repo.upsert_companies([Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930")])
    uid = repo.create_user("a@b.co", "h")
    other = repo.create_user("c@d.co", "h")
    repo.set_watch("00126380", 2, uid)
    a = "https://fcm.googleapis.com/fcm/send/a"
    b = "https://updates.push.services.mozilla.com/wpush/v2/b"

    first = repo.add_push_subscription(uid, a, "pa", "aa", "V1", "Chrome · Windows")
    repo.add_push_subscription(uid, b, "pb", "ab", "V1", "Firefox · macOS")
    assert [s["endpoint"] for s in repo.push_subscriptions(uid)] == [a, b]
    [ch] = repo.alert_channels(uid)
    assert ch["kind"] == "push" and ch["verified"] and ch["enabled"] and ch["target"] is None
    devices = repo.push_devices(uid)
    assert devices[0]["key"] == hashlib.sha256(a.encode()).hexdigest()
    assert devices[0]["label"] == "Chrome · Windows" and devices[0]["last_sent_at"] is None

    repo.insert_disclosures([_disclosure("20250311000001")])
    pending = repo.user_pending_alerts()
    assert [(p["user_id"], p["kind"], p["target"]) for p in pending] == [(uid, "push", None)]

    # 보낸 곳은 시각을 적고, 끝난 구독은 지운다
    repo.record_push_results(uid, [first], [])
    assert repo.push_devices(uid)[0]["last_sent_at"] is not None
    second = repo.push_subscriptions(uid)[1]["id"]
    repo.record_push_results(uid, [], [second])
    assert [s["endpoint"] for s in repo.push_subscriptions(uid)] == [a]

    # 같은 브라우저를 다른 사용자가 구독하면 옮겨 가고, 구독이 없는 채널은 사라진다
    repo.add_push_subscription(other, a, "pa2", "aa2", "V1", "Chrome · Windows")
    assert repo.push_subscriptions(uid) == [] and repo.alert_channels(uid) == []
    assert repo.user_pending_alerts() == []  # 구독이 없으면 보낼 것도 없다
    assert repo.push_subscriptions(other)[0]["p256dh"] == "pa2"

    # 사용자마다 최대 개수를 넘으면 오래된 것부터 지운다
    for i in range(4):
        repo.add_push_subscription(uid, f"{a}-{i}", "p", "a", "V1", "x", max_per_user=3)
    assert [s["endpoint"][-1] for s in repo.push_subscriptions(uid)] == ["1", "2", "3"]

    # 하나씩 해제 (다른 사용자 것은 지울 수 없다), 채널째 지우기
    assert not repo.remove_push_subscription(other, endpoint=f"{a}-1")
    assert repo.remove_push_subscription(uid, endpoint=f"{a}-1")
    sid = repo.push_subscriptions(uid)[0]["id"]
    assert repo.remove_push_subscription(uid, sub_id=sid)
    assert repo.set_alert_enabled(uid, "push", False)
    assert repo.remove_alert_channel(uid, "push") and repo.push_subscriptions(uid) == []
    assert len(repo.push_subscriptions(other)) == 1


def test_two_factor_storage(repo):
    from datetime import UTC, datetime, timedelta

    uid = repo.create_user("a@b.co", "h")
    later = datetime.now(UTC) + timedelta(minutes=5)
    assert repo.totp(uid) is None and not repo.totp_enabled(uid)
    assert repo.start_totp(uid, "v1:first") and repo.start_totp(uid, "v1:second")
    state = repo.totp(uid)
    assert state == {"secret": "v1:second", "enabled": False, "last_step": None, "recovery_left": 0}
    assert not repo.use_totp_step(uid, 5)  # 켜기 전에는 로그인에 쓰지 않는다

    repo.create_session("s" * 64, uid, later)
    assert repo.enable_totp(uid, 10, ["1" * 64, "2" * 64])
    assert not repo.enable_totp(uid, 11, ["3" * 64])
    assert repo.session_user("s" * 64) is None  # 켜면 다른 로그인은 끊는다
    assert repo.totp_enabled(uid) and repo.totp(uid)["recovery_left"] == 2
    assert not repo.start_totp(uid, "v1:third")  # 켠 상태에서는 비밀값을 바꾸지 않는다
    assert repo.totp(uid)["secret"] == "v1:second"

    # 같은 구간이나 앞 구간 코드는 다시 쓸 수 없다
    assert not repo.use_totp_step(uid, 10) and not repo.use_totp_step(uid, 9)
    assert repo.use_totp_step(uid, 11) and not repo.use_totp_step(uid, 11)
    assert repo.use_recovery_code(uid, "1" * 64) == 1
    assert repo.use_recovery_code(uid, "1" * 64) is None
    repo.replace_recovery_codes(uid, ["4" * 64, "5" * 64, "6" * 64])
    assert repo.use_recovery_code(uid, "2" * 64) is None and repo.totp(uid)["recovery_left"] == 3

    # 로그인 중간 단계: 기한, 틀린 횟수
    repo.create_login_challenge("c" * 64, uid, later)
    assert repo.login_challenge("c" * 64, 3) == (uid, "a@b.co")
    assert repo.fail_login_challenge("c" * 64, 3) == 2
    assert repo.fail_login_challenge("c" * 64, 3) == 1
    assert repo.fail_login_challenge("c" * 64, 3) == 0
    assert repo.login_challenge("c" * 64, 3) is None
    repo.create_login_challenge("d" * 64, uid, datetime.now(UTC) - timedelta(seconds=1))
    assert repo.login_challenge("d" * 64, 3) is None
    repo.create_login_challenge("e" * 64, uid, later)  # 만료된 단계는 이때 지운다
    assert _count(repo, "login_challenges") == 1
    repo.delete_login_challenge("e" * 64)
    assert _count(repo, "login_challenges") == 0

    repo.create_login_challenge("f" * 64, uid, later)
    repo.create_session("t" * 64, uid, later)
    assert repo.revoke_sessions(uid) == 1 and repo.session_user("t" * 64) is None
    assert repo.disable_totp(uid) and not repo.disable_totp(uid)
    assert repo.totp(uid) is None
    assert _count(repo, "user_recovery_codes") == 0 and _count(repo, "login_challenges") == 0
    assert repo.export_user(uid)["two_factor"] == {"enabled": False}


def test_migrations_013_014(repo):
    """push 채널 종류, 새 표의 외래 키, 다시 돌려도 되는지."""
    uid = repo.create_user("a@b.co", "h")
    repo.migrate()
    repo.migrate()
    defs = repo.conn.execute(
        """SELECT pg_get_constraintdef(oid) FROM pg_constraint
           WHERE conname = 'user_alert_channels_kind_check'"""
    ).fetchall()
    assert len(defs) == 1 and "push" in defs[0][0]
    repo.conn.execute(
        "INSERT INTO user_alert_channels (user_id, kind, verified_at) VALUES (%s, 'push', now())",
        (uid,),
    )
    repo.conn.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        repo.conn.execute(
            "INSERT INTO user_alert_channels (user_id, kind) VALUES (%s, 'sms')", (uid,)
        )
    repo.conn.rollback()
    with pytest.raises(psycopg.errors.UniqueViolation):
        for _ in range(2):
            repo.conn.execute(
                """INSERT INTO user_push_subscriptions (user_id, endpoint, p256dh, auth, vapid_key)
                   VALUES (%s, 'https://fcm.googleapis.com/x', 'p', 'a', 'v')""",
                (uid,),
            )
    repo.conn.rollback()
    tables = {t for t, _ in _user_references(repo)}
    new = {"user_push_subscriptions", "user_totp", "user_recovery_codes", "login_challenges"}
    assert new <= tables


def test_company_versions_follow_dashboard_data(repo):
    from dataclasses import replace

    from dartrag.finance.validate import Issue

    repo.upsert_companies(
        [
            Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930"),
            Corp(corp_code="00164779", corp_name="SK하이닉스", stock_code="000660"),
        ]
    )
    code = "00126380"
    seen = [repo.company_version(code)]

    def changed() -> bool:
        seen.append(repo.company_version(code))
        return seen[-1] != seen[-2]

    other = repo.company_version("00164779")
    # 재무 수치를 바꾸면 오른다
    repo.replace_financials(code, 2024, "11011", "CFS", [item(100)])
    assert changed()
    # 새 공시가 들어오면 오르고, 이미 있던 공시를 다시 받으면 그대로
    assert repo.insert_disclosures([_disclosure("20250311000001")]) == ["20250311000001"]
    assert changed()
    assert repo.insert_disclosures([_disclosure("20250311000001")]) == []
    assert not changed()
    # 검증 결과는 화면에 보이는 내용이 달라질 때만
    issue = Issue(code, 2024, "11011", "CFS", "balance", "error", "어긋남")
    repo.replace_issues(code, [issue])
    assert changed()
    repo.replace_issues(code, [issue])
    assert not changed()
    repo.replace_issues(code, (i for i in [replace(issue, detail="더 어긋남")]))
    assert changed() and repo.data_issues(code)[0]["detail"] == "더 어긋남"
    repo.replace_issues(code, [])
    assert changed()
    # 다른 회사, 없는 회사는 영향 없음
    assert repo.company_version("00164779") == other
    assert repo.company_version("00000000") == "0"
    # 회사 정보(이름)가 바뀌어도 달라진다
    repo.upsert_companies([Corp(corp_code=code, corp_name="삼성전자(주)", stock_code="005930")])
    assert changed()


def test_filing_freshness_and_dashboard_targets(repo):
    repo.upsert_companies(
        [
            Corp(corp_code="00126380", corp_name="삼성전자", stock_code="005930"),
            Corp(corp_code="00164779", corp_name="SK하이닉스", stock_code="000660"),
        ]
    )

    def filing(no, name, day, corp="00126380"):
        f = Filing(corp_code=corp, corp_name="x", report_nm=name, rcept_no=no, rcept_dt=day)
        repo.upsert_filing(f, parse_report_name(name), "11011", None)

    filing("20250311000001", "사업보고서 (2024.12)", date(2025, 3, 11))
    filing("20250814000002", "반기보고서 (2025.06)", date(2025, 8, 14))
    filing("20250312000003", "사업보고서 (2024.12)", date(2025, 3, 12), corp="00164779")
    repo.mark_indexed("20250311000001", 1, "m")
    repo.mark_indexed("20250312000003", 1, "m")

    got = repo.filing_freshness(["20250311000001", "20250312000003", "29991231999999"])
    assert set(got) == {"20250311000001", "20250312000003"}
    samsung = got["20250311000001"]
    assert samsung["corp_code"] == "00126380" and samsung["rcept_dt"] == date(2025, 3, 11)
    # 같은 회사의 더 최근 정기공시(아직 색인 전)
    assert samsung["latest"] == {
        "rcept_no": "20250814000002",
        "report_nm": "반기보고서 (2025.06)",
        "rcept_dt": date(2025, 8, 14),
        "indexed": False,
    }
    assert got["20250312000003"]["latest"]["rcept_no"] == "20250312000003"
    assert repo.filing_freshness([]) == {}

    # 미리 만들 대시보드: 운영자나 사용자 누군가의 관심 종목
    assert repo.watched_companies() == []
    repo.set_watch("00164779", 2)
    uid = repo.create_user("a@b.co", "h")
    repo.set_watch("00126380", 2, uid)
    repo.set_watch("00164779", 3, uid)
    # 회사 이름 정렬은 DB 로캘마다 달라서 회사 코드 순서로 돌려준다
    assert repo.watched_companies() == [
        ("00126380", "삼성전자", "005930"),
        ("00164779", "SK하이닉스", "000660"),
    ]
    assert repo.companies_by_code(["00126380", "99999999"]) == [("00126380", "삼성전자", "005930")]
