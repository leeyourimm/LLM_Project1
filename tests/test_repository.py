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
    conn.execute("DROP TABLE IF EXISTS financial_items, filings, companies CASCADE")
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
