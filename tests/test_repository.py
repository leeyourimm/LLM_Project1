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
    conn.execute("DROP TABLE IF EXISTS chunks, financial_items, filings, companies CASCADE")
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
