from collections.abc import Iterable
from pathlib import Path

import psycopg

from dartrag.dart.models import Corp, Filing
from dartrag.dart.reports import PeriodicReport

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "infra" / "db"


class Repository:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    @classmethod
    def connect(cls, url: str) -> "Repository":
        return cls(psycopg.connect(url))

    def migrate(self) -> None:
        for path in sorted(SCHEMA_DIR.glob("*.sql")):
            self.conn.execute(path.read_text())
        self.conn.commit()

    def upsert_companies(self, corps: Iterable[Corp]) -> int:
        rows = [
            (c.corp_code, c.corp_name, c.corp_eng_name, c.stock_code, c.modify_date) for c in corps
        ]
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO companies (corp_code, corp_name, corp_eng_name, stock_code, modify_date)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (corp_code) DO UPDATE SET
                    corp_name = EXCLUDED.corp_name,
                    corp_eng_name = EXCLUDED.corp_eng_name,
                    stock_code = EXCLUDED.stock_code,
                    modify_date = EXCLUDED.modify_date,
                    updated_at = now()
                """,
                rows,
            )
        self.conn.commit()
        return len(rows)

    def fiscal_end_month(self, corp_code: str) -> int:
        row = self.conn.execute(
            "SELECT fiscal_end_month FROM companies WHERE corp_code = %s", (corp_code,)
        ).fetchone()
        return row[0] if row else 12

    def upsert_filing(
        self,
        filing: Filing,
        report: PeriodicReport | None,
        reprt_code: str | None,
        raw_key: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO filings (rcept_no, corp_code, report_nm, report_kind, period_key,
                                 reprt_code, is_correction, rcept_dt, raw_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (rcept_no) DO UPDATE SET
                raw_key = COALESCE(EXCLUDED.raw_key, filings.raw_key),
                collected_at = now()
            """,
            (
                filing.rcept_no,
                filing.corp_code,
                filing.report_nm,
                report.kind if report else None,
                report.period_key if report else None,
                reprt_code,
                report.is_correction if report else False,
                filing.rcept_dt,
                raw_key,
            ),
        )
        self.conn.commit()

    def replace_financials(
        self, corp_code: str, bsns_year: int, reprt_code: str, fs_div: str, items: list[dict]
    ) -> int:
        """한 보고서·한 재무제표 구분의 수치를 통째로 교체 (정정공시 반영, 재실행 안전)."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """DELETE FROM financial_items
                   WHERE corp_code = %s AND bsns_year = %s AND reprt_code = %s AND fs_div = %s""",
                (corp_code, bsns_year, reprt_code, fs_div),
            )
            cur.executemany(
                """
                INSERT INTO financial_items (corp_code, bsns_year, reprt_code, fs_div, sj_div,
                    account_id, account_nm, account_detail, ord, amount, add_amount, currency,
                    raw_amount, raw_add_amount, rcept_no)
                VALUES (%(corp_code)s, %(bsns_year)s, %(reprt_code)s, %(fs_div)s, %(sj_div)s,
                    %(account_id)s, %(account_nm)s, %(account_detail)s, %(ord)s, %(amount)s,
                    %(add_amount)s, %(currency)s, %(raw_amount)s, %(raw_add_amount)s, %(rcept_no)s)
                """,
                items,
            )
        return len(items)
