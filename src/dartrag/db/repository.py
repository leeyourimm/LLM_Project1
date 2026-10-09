from collections.abc import Iterable
from pathlib import Path

import psycopg

from dartrag.dart.models import Corp, Filing
from dartrag.dart.reports import PeriodicReport
from dartrag.parsing import Chunk
from dartrag.search.types import IndexedChunk

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

    def corp_codes_for_stocks(self, stock_codes: list[str]) -> list[str]:
        rows = self.conn.execute(
            "SELECT corp_code FROM companies WHERE stock_code = ANY(%s)", (stock_codes,)
        ).fetchall()
        return [r[0] for r in rows]

    def company_by_stock(self, stock_code: str) -> tuple[str, str, str] | None:
        return self.conn.execute(
            "SELECT corp_code, corp_name, stock_code FROM companies WHERE stock_code = %s",
            (stock_code,),
        ).fetchone()

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
            self._bump_data_version(cur)
            self._bump_company_versions(cur, [corp_code])
        return len(items)

    def filings_to_parse(self, parser_version: int, corp_codes: list[str] | None = None):
        """원문이 있고 아직 이 버전 파서로 처리하지 않은 공시."""
        query = """
            SELECT f.rcept_no, f.corp_code, c.corp_name, f.report_kind, f.period_key, f.raw_key
            FROM filings f JOIN companies c USING (corp_code)
            WHERE f.raw_key IS NOT NULL
              AND (f.parser_version IS NULL OR f.parser_version < %s)
        """
        params: list = [parser_version]
        if corp_codes:
            query += " AND f.corp_code = ANY(%s)"
            params.append(corp_codes)
        return self.conn.execute(query + " ORDER BY f.rcept_dt", params).fetchall()

    def replace_chunks(
        self,
        rcept_no: str,
        corp_code: str,
        chunks_by_file: list[tuple[str, list[Chunk]]],
        parser_version: int,
    ) -> int:
        """한 공시의 청크를 통째로 교체하고 파싱 완료로 표시."""
        rows = [
            {
                "chunk_id": c.chunk_id,
                "rcept_no": rcept_no,
                "corp_code": corp_code,
                "source_file": source_file,
                "ord": c.ord,
                "kind": c.kind,
                "section_path": c.section_path,
                "context": c.context,
                "body": c.body,
                "unit": c.unit,
                "char_count": len(c.body),
            }
            for source_file, chunks in chunks_by_file
            for c in chunks
        ]
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE rcept_no = %s", (rcept_no,))
            cur.executemany(
                """
                INSERT INTO chunks (chunk_id, rcept_no, corp_code, source_file, ord, kind,
                    section_path, context, body, unit, char_count)
                VALUES (%(chunk_id)s, %(rcept_no)s, %(corp_code)s, %(source_file)s, %(ord)s,
                    %(kind)s, %(section_path)s, %(context)s, %(body)s, %(unit)s, %(char_count)s)
                """,
                rows,
            )
            cur.execute(
                "UPDATE filings SET parsed_at = now(), parser_version = %s WHERE rcept_no = %s",
                (parser_version, rcept_no),
            )
        return len(rows)

    def filings_to_index(
        self, index_version: int, index_model: str, corp_codes: list[str] | None = None
    ) -> list[str]:
        """파싱은 됐지만 아직 색인하지 않았거나, 재파싱·모델 변경으로 다시 색인해야 하는 공시."""
        query = """
            SELECT rcept_no FROM filings
            WHERE parsed_at IS NOT NULL
              AND (indexed_at IS NULL OR indexed_at < parsed_at
                   OR index_version IS DISTINCT FROM %s OR index_model IS DISTINCT FROM %s)
        """
        params: list = [index_version, index_model]
        if corp_codes:
            query += " AND corp_code = ANY(%s)"
            params.append(corp_codes)
        rows = self.conn.execute(query + " ORDER BY rcept_dt, rcept_no", params).fetchall()
        return [r[0] for r in rows]

    def indexed_chunks(self, rcept_no: str) -> list[IndexedChunk]:
        rows = self.conn.execute(
            """
            SELECT ch.chunk_id, ch.rcept_no, ch.corp_code, co.corp_name, f.report_kind,
                   f.period_key, ch.kind, ch.section_path, ch.context, ch.body
            FROM chunks ch
            JOIN filings f USING (rcept_no)
            JOIN companies co ON co.corp_code = ch.corp_code
            WHERE ch.rcept_no = %s
            ORDER BY ch.source_file, ch.ord
            """,
            (rcept_no,),
        ).fetchall()
        return [
            IndexedChunk(
                chunk_id=r[0],
                rcept_no=r[1],
                corp_code=r[2],
                corp_name=r[3],
                report_kind=r[4],
                period_key=r[5],
                kind=r[6],
                section_path=list(r[7]),
                text=f"{r[8]}\n\n{r[9]}",
            )
            for r in rows
        ]

    def has_indexed_filings(self) -> bool:
        """검색할 공시가 하나라도 색인돼 있는지. 없으면 질문에 바로 안내한다."""
        row = self.conn.execute(
            "SELECT EXISTS (SELECT 1 FROM filings WHERE indexed_at IS NOT NULL)"
        ).fetchone()
        return bool(row[0])

    def mark_indexed(self, rcept_no: str, index_version: int, index_model: str) -> None:
        self.conn.execute(
            """UPDATE filings SET indexed_at = now(), index_version = %s, index_model = %s
               WHERE rcept_no = %s""",
            (index_version, index_model, rcept_no),
        )
        self._bump_data_version(self.conn)
        self.conn.commit()

    # --- 데이터 버전 (답변 캐시 무효화용) ----------------------------------

    @staticmethod
    def _bump_data_version(executor) -> None:
        executor.execute(
            """INSERT INTO app_state (key, value) VALUES ('data_version', 1)
               ON CONFLICT (key) DO UPDATE SET value = app_state.value + 1, updated_at = now()"""
        )

    def data_version(self) -> int:
        row = self.conn.execute("SELECT value FROM app_state WHERE key = 'data_version'").fetchone()
        return row[0] if row else 0

    # --- 회사별 데이터 버전 (기업 대시보드 캐시 무효화용) ---------------------

    @staticmethod
    def _bump_company_versions(executor, corp_codes) -> None:
        """재무 수치, 주요 공시, 검증 결과처럼 대시보드에 보이는 회사 데이터가 바뀔 때 부른다."""
        codes = sorted(set(corp_codes))
        if codes:
            executor.execute(
                """INSERT INTO company_versions (corp_code)
                   SELECT unnest(%s::text[])
                   ON CONFLICT (corp_code) DO UPDATE
                   SET version = company_versions.version + 1, updated_at = now()""",
                (codes,),
            )

    def company_version(self, corp_code: str) -> str:
        """캐시 키용 회사 데이터 버전. 회사 정보(이름 등)가 바뀌어도 달라진다."""
        row = self.conn.execute(
            """SELECT COALESCE(v.version, 0), c.updated_at
               FROM companies c LEFT JOIN company_versions v USING (corp_code)
               WHERE c.corp_code = %s""",
            (corp_code,),
        ).fetchone()
        if row is None:
            return "0"
        return f"{row[0]}.{round(row[1].timestamp() * 1_000_000)}"

    def watched_companies(self) -> list[tuple[str, str, str]]:
        """운영자나 사용자 누군가가 관심 종목에 넣은 상장사 (대시보드 미리 만들기 대상)."""
        return self.conn.execute(
            """SELECT c.corp_code, c.corp_name, c.stock_code FROM companies c
               WHERE c.stock_code IS NOT NULL AND (
                   EXISTS (SELECT 1 FROM watchlist w WHERE w.corp_code = c.corp_code)
                   OR EXISTS (SELECT 1 FROM user_watchlist u WHERE u.corp_code = c.corp_code))
               ORDER BY c.corp_code"""
        ).fetchall()

    def companies_by_code(self, corp_codes: list[str]) -> list[tuple[str, str, str]]:
        return self.conn.execute(
            """SELECT corp_code, corp_name, stock_code FROM companies
               WHERE corp_code = ANY(%s) AND stock_code IS NOT NULL ORDER BY corp_code""",
            (corp_codes,),
        ).fetchall()

    def filing_freshness(self, rcept_nos: list[str]) -> dict[str, dict]:
        """인용한 공시마다 그 공시의 정보와, 같은 회사의 가장 최근 정기공시.

        답변 신뢰도 표시에 쓴다 (인용한 자료가 최신 공시인지, 얼마나 오래됐는지).
        정정공시도 따로 접수되므로 원본을 인용했는데 정정본이 있으면 최신이 아니다."""
        if not rcept_nos:
            return {}
        rows = self.conn.execute(
            """
            SELECT f.rcept_no, f.corp_code, c.corp_name, f.report_nm, f.rcept_dt,
                   l.rcept_no, l.report_nm, l.rcept_dt, l.indexed_at IS NOT NULL
            FROM filings f
            JOIN companies c USING (corp_code)
            LEFT JOIN LATERAL (
                SELECT rcept_no, report_nm, rcept_dt, indexed_at FROM filings
                WHERE corp_code = f.corp_code AND report_kind IS NOT NULL
                ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 1
            ) l ON true
            WHERE f.rcept_no = ANY(%s)
            """,
            (list(rcept_nos),),
        ).fetchall()
        out = {}
        for r in rows:
            latest = None
            if r[5] is not None:
                latest = {"rcept_no": r[5], "report_nm": r[6], "rcept_dt": r[7], "indexed": r[8]}
            out[r[0]] = {
                "rcept_no": r[0],
                "corp_code": r[1],
                "corp_name": r[2],
                "report_nm": r[3],
                "rcept_dt": r[4],
                "latest": latest,
            }
        return out

    def expand_chunks(
        self, chunk_ids: list[str], window: int = 1, max_chars: int = 3000
    ) -> dict[str, str]:
        """같은 섹션의 앞뒤 청크를 붙인 넓은 맥락 (검색은 작게, 답변 근거는 넓게)."""
        rows = self.conn.execute(
            """
            SELECT c.chunk_id, c.ord, n.ord, n.body
            FROM chunks c
            JOIN chunks n ON n.rcept_no = c.rcept_no AND n.source_file = c.source_file
                 AND n.section_path = c.section_path
                 AND n.ord BETWEEN c.ord - %s AND c.ord + %s
            WHERE c.chunk_id = ANY(%s)
            ORDER BY c.chunk_id, n.ord
            """,
            (window, window, chunk_ids),
        ).fetchall()
        groups: dict[str, list[tuple[int, int, str]]] = {}
        for cid, center, ord_, body in rows:
            groups.setdefault(cid, []).append((center, ord_, body))
        out = {}
        for cid, parts in groups.items():
            if len(parts) < 2:
                continue
            center = parts[0][0]
            by_ord = {o: b for _, o, b in parts}
            keep = [center]
            size = len(by_ord[center])
            # 가까운 것부터 붙이고 max_chars 를 넘기면 멈춘다
            for o in sorted(by_ord, key=lambda o: (abs(o - center), o)):
                if o == center or size + len(by_ord[o]) > max_chars:
                    continue
                keep.append(o)
                size += len(by_ord[o])
            if len(keep) > 1:
                out[cid] = "\n".join(by_ord[o] for o in sorted(keep))
        return out

    def get_chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        """검색 결과에 붙일 청크 본문과 출처."""
        rows = self.conn.execute(
            """
            SELECT ch.chunk_id, ch.rcept_no, co.corp_name, f.report_nm, f.report_kind,
                   f.period_key, f.rcept_dt, ch.kind, ch.section_path, ch.body, ch.unit
            FROM chunks ch
            JOIN filings f USING (rcept_no)
            JOIN companies co ON co.corp_code = ch.corp_code
            WHERE ch.chunk_id = ANY(%s)
            """,
            (chunk_ids,),
        ).fetchall()
        keys = (
            "chunk_id",
            "rcept_no",
            "corp_name",
            "report_nm",
            "report_kind",
            "period_key",
            "rcept_dt",
            "kind",
            "section_path",
            "body",
            "unit",
        )
        out = {}
        for r in rows:
            d = dict(zip(keys, r, strict=True))
            d["section_path"] = list(d["section_path"])
            d["url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d['rcept_no']}"
            out[d["chunk_id"]] = d
        return out

    def listed_companies(self) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            "SELECT corp_code, corp_name FROM companies WHERE stock_code IS NOT NULL"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def listed_companies_with_stock(self) -> list[tuple[str, str, str]]:
        return self.conn.execute(
            """SELECT corp_code, corp_name, stock_code FROM companies
               WHERE stock_code IS NOT NULL ORDER BY corp_name"""
        ).fetchall()

    def financial_rows(
        self,
        corp_codes: list[str],
        reprt_code: str,
        sj_divs: tuple[str, ...],
        account_ids: tuple[str, ...],
        account_names: tuple[str, ...],
    ):
        """지표 후보 계정 행. 어느 행을 쓸지는 finance.tool.pick_values 가 고른다."""
        from dartrag.finance import FinancialRow

        rows = self.conn.execute(
            """
            SELECT corp_code, bsns_year, fs_div, account_id,
                   regexp_replace(account_nm, '\\s', '', 'g') AS nm, amount, rcept_no, ord
            FROM financial_items
            WHERE corp_code = ANY(%s) AND reprt_code = %s AND sj_div = ANY(%s)
              AND (account_id = ANY(%s) OR regexp_replace(account_nm, '\\s', '', 'g') = ANY(%s))
            """,
            (corp_codes, reprt_code, list(sj_divs), list(account_ids), list(account_names)),
        ).fetchall()
        return [FinancialRow(*r) for r in rows]

    def quarter_rows(
        self,
        corp_code: str,
        sj_divs: tuple[str, ...],
        account_ids: tuple[str, ...],
        account_names: tuple[str, ...],
    ):
        """분기 추이용: 사업·반기·분기 보고서의 후보 계정 행 (누적 금액 포함)."""
        from dartrag.finance import FinancialRow

        rows = self.conn.execute(
            """
            SELECT corp_code, bsns_year, fs_div, account_id,
                   regexp_replace(account_nm, '\\s', '', 'g') AS nm, amount, rcept_no, ord,
                   reprt_code, add_amount
            FROM financial_items
            WHERE corp_code = %s AND sj_div = ANY(%s)
              AND (account_id = ANY(%s) OR regexp_replace(account_nm, '\\s', '', 'g') = ANY(%s))
            """,
            (corp_code, list(sj_divs), list(account_ids), list(account_names)),
        ).fetchall()
        return [FinancialRow(*r) for r in rows]

    def filing_info(self, rcept_no: str) -> dict | None:
        row = self.conn.execute(
            """SELECT f.rcept_no, f.report_nm, f.rcept_dt, c.corp_name, f.parsed_at IS NOT NULL
               FROM filings f JOIN companies c USING (corp_code) WHERE f.rcept_no = %s""",
            (rcept_no,),
        ).fetchone()
        if row is None:
            return None
        keys = ("rcept_no", "report_nm", "rcept_dt", "corp_name", "parsed")
        return dict(zip(keys, row, strict=True))

    def chunk_rows(self, rcept_no: str):
        return self.conn.execute(
            "SELECT source_file, ord, section_path, body FROM chunks WHERE rcept_no = %s",
            (rcept_no,),
        ).fetchall()

    def latest_filing_per_period(self, corp_code: str, report_kind: str) -> list[str]:
        """기간별 마지막 접수본(정정 반영)의 접수번호, 오래된 기간부터."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT ON (period_key) rcept_no
            FROM filings
            WHERE corp_code = %s AND report_kind = %s AND parsed_at IS NOT NULL
            ORDER BY period_key, rcept_dt DESC, rcept_no DESC
            """,
            (corp_code, report_kind),
        ).fetchall()
        return [r[0] for r in rows]

    def insert_disclosures(self, rows: list[dict]) -> list[str]:
        """새 공시만 넣고, 새로 들어간 접수번호를 돌려준다."""
        new: list[str] = []
        with self.conn.transaction(), self.conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO disclosures (rcept_no, corp_code, corp_name, stock_code, corp_cls,
                        report_nm, flr_nm, rcept_dt, rm, pblntf_ty, event_type, event_label,
                        importance, correction)
                    VALUES (%(rcept_no)s, %(corp_code)s, %(corp_name)s, %(stock_code)s,
                        %(corp_cls)s, %(report_nm)s, %(flr_nm)s, %(rcept_dt)s, %(rm)s,
                        %(pblntf_ty)s, %(event_type)s, %(event_label)s, %(importance)s,
                        %(correction)s)
                    ON CONFLICT (rcept_no) DO NOTHING
                    RETURNING rcept_no
                    """,
                    r,
                )
                if cur.fetchone():
                    new.append(r["rcept_no"])
            self._bump_company_versions(
                cur, [r["corp_code"] for r in rows if r["rcept_no"] in set(new)]
            )
        return new

    def recent_disclosures(
        self, since, min_importance: int = 1, corp_codes: list[str] | None = None
    ) -> list[dict]:
        query = """
            SELECT rcept_no, corp_code, corp_name, stock_code, report_nm, rcept_dt,
                   event_label, importance, correction
            FROM disclosures WHERE rcept_dt >= %s AND importance >= %s
        """
        params: list = [since, min_importance]
        if corp_codes:
            query += " AND corp_code = ANY(%s)"
            params.append(corp_codes)
        query += " ORDER BY rcept_dt DESC, importance DESC, rcept_no DESC"
        cur = self.conn.execute(query, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    # 관심 종목: user_id 가 없으면 운영자 목록(watchlist), 있으면 그 사용자 목록(user_watchlist)

    def set_watch(self, corp_code: str, min_importance: int, user_id: int | None = None) -> None:
        if user_id is None:
            self.conn.execute(
                """INSERT INTO watchlist (corp_code, min_importance) VALUES (%s, %s)
                   ON CONFLICT (corp_code)
                   DO UPDATE SET min_importance = EXCLUDED.min_importance""",
                (corp_code, min_importance),
            )
        else:
            self.conn.execute(
                """INSERT INTO user_watchlist (user_id, corp_code, min_importance)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (user_id, corp_code)
                   DO UPDATE SET min_importance = EXCLUDED.min_importance""",
                (user_id, corp_code, min_importance),
            )
        self.conn.commit()

    def remove_watch(self, corp_code: str, user_id: int | None = None) -> bool:
        if user_id is None:
            cur = self.conn.execute("DELETE FROM watchlist WHERE corp_code = %s", (corp_code,))
        else:
            cur = self.conn.execute(
                "DELETE FROM user_watchlist WHERE user_id = %s AND corp_code = %s",
                (user_id, corp_code),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def watchlist(self, user_id: int | None = None) -> list[tuple[str, str, str | None, int]]:
        if user_id is None:
            source, params = "watchlist w", ()
        else:
            source, params = "user_watchlist w", (user_id,)
        where = "" if user_id is None else "WHERE w.user_id = %s"
        return self.conn.execute(
            f"""SELECT w.corp_code, COALESCE(c.corp_name, w.corp_code), c.stock_code,
                       w.min_importance
                FROM {source} LEFT JOIN companies c USING (corp_code)
                {where}
                ORDER BY c.corp_name""",
            params,
        ).fetchall()

    # --- 로그인 ---------------------------------------------------------

    def create_user(self, email: str, password_hash: str) -> int | None:
        """새 사용자 id. 이미 있는 이메일이면 None."""
        row = self.conn.execute(
            """INSERT INTO users (email, password_hash) VALUES (%s, %s)
               ON CONFLICT (email) DO NOTHING RETURNING id""",
            (email, password_hash),
        ).fetchone()
        self.conn.commit()
        return row[0] if row else None

    def user_by_email(self, email: str) -> tuple[int, str, str] | None:
        return self.conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = %s", (email,)
        ).fetchone()

    def set_password(self, user_id: int, password_hash: str) -> None:
        self.conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id)
        )
        # 비밀번호를 바꾸면 다른 기기의 로그인도 모두 끊는다
        self.conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        self.conn.commit()

    def users(self) -> list[tuple[int, str, object, object]]:
        """가입한 계정 (체험 계정은 빼고)."""
        return self.conn.execute(
            """SELECT id, email, created_at, last_login_at FROM users
               WHERE guest_expires_at IS NULL ORDER BY id"""
        ).fetchall()

    # --- 체험 계정 (가입 없이 체험하기) ------------------------------------

    def create_guest(self, expires_at) -> int:
        """이메일·비밀번호 없는 체험 계정. expires_at 이 지나면 로그인이 끊기고 지울 대상이 된다."""
        row = self.conn.execute(
            "INSERT INTO users (guest_expires_at) VALUES (%s) RETURNING id", (expires_at,)
        ).fetchone()
        self.conn.commit()
        return row[0]

    def upgrade_guest(self, user_id: int, email: str, password_hash: str) -> bool:
        """체험 중에 가입: 같은 계정에 이메일·비밀번호를 넣어 대화 기록 등을 그대로 이어 쓴다.

        체험 계정의 로그인은 모두 끊는다 (부른 쪽이 새 세션을 만든다). 이미 가입된 이메일이거나
        체험 계정이 아니거나 기한이 지났으면 False."""
        try:
            cur = self.conn.execute(
                """UPDATE users SET email = %s, password_hash = %s, guest_expires_at = NULL
                   WHERE id = %s AND guest_expires_at > now()""",
                (email, password_hash, user_id),
            )
            if cur.rowcount:
                self.conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            self.conn.commit()
        except psycopg.errors.UniqueViolation:
            self.conn.rollback()
            return False
        return cur.rowcount > 0

    def expired_guests(self, limit: int = 500) -> list[int]:
        """기한이 지난 체험 계정 id (오래된 것부터)."""
        rows = self.conn.execute(
            """SELECT id FROM users WHERE guest_expires_at <= now()
               ORDER BY guest_expires_at, id LIMIT %s""",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    def remove_user(self, email: str) -> bool:
        """이메일로 계정 삭제 (CLI). 화면의 탈퇴와 같은 delete_user 를 쓴다."""
        row = self.conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
        return self.delete_user(row[0]) if row else False

    # 사용자 기록이 든 표. 순서대로 지운다 (평가 → 메시지 → 대화 → … → 계정)
    _USER_DELETES = (
        """DELETE FROM feedback WHERE message_id IN (
               SELECT m.id FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE c.user_id = %s)""",
        """DELETE FROM messages WHERE conversation_id IN (
               SELECT id FROM conversations WHERE user_id = %s)""",
        "DELETE FROM conversations WHERE user_id = %s",
        "DELETE FROM user_notifications WHERE user_id = %s",
        "DELETE FROM user_push_subscriptions WHERE user_id = %s",
        "DELETE FROM user_alert_channels WHERE user_id = %s",
        "DELETE FROM user_watchlist WHERE user_id = %s",
        "DELETE FROM auth_tokens WHERE user_id = %s",
        "DELETE FROM user_devices WHERE user_id = %s",
        "DELETE FROM login_challenges WHERE user_id = %s",
        "DELETE FROM user_recovery_codes WHERE user_id = %s",
        "DELETE FROM user_totp WHERE user_id = %s",
        "DELETE FROM sessions WHERE user_id = %s",
    )

    def delete_user(self, user_id: int) -> bool:
        """계정과 그 사용자의 기록을 한 트랜잭션에서 모두 지운다.

        외래 키의 ON DELETE CASCADE 로도 지워지지만, 나중에 표를 더하다 빠뜨려도
        기록이 남지 않게 직접 지운다. 하나라도 실패하면 아무것도 지우지 않는다."""
        try:
            for sql in self._USER_DELETES:
                self.conn.execute(sql, (user_id,))
            cur = self.conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return cur.rowcount > 0

    def export_user(self, user_id: int) -> dict | None:
        """내 데이터 내려받기용. 비밀번호 해시, 세션, 인증 코드·기기 해시, 2단계 인증 비밀값과
        복구 코드, 웹 푸시 구독 주소·키는 넣지 않는다."""

        def rows(sql: str, params: tuple) -> list[dict]:
            cur = self.conn.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        found = rows(
            """SELECT email, email_verified_at, created_at, last_login_at
               FROM users WHERE id = %s""",
            (user_id,),
        )
        if not found:
            return None
        conversations = rows(
            """SELECT id, title, created_at, updated_at FROM conversations
               WHERE user_id = %s ORDER BY created_at, id""",
            (user_id,),
        )
        messages = rows(
            """SELECT m.conversation_id, m.id, m.role, m.content, m.payload, m.created_at,
                      f.rating AS feedback_rating, f.reason AS feedback_reason,
                      f.comment AS feedback_comment, f.created_at AS feedback_at
               FROM messages m JOIN conversations c ON c.id = m.conversation_id
               LEFT JOIN feedback f ON f.message_id = m.id
               WHERE c.user_id = %s ORDER BY m.id""",
            (user_id,),
        )
        by_conv: dict[int, list[dict]] = {}
        for m in messages:
            fb = {k: m.pop(f"feedback_{k}") for k in ("rating", "reason", "comment", "at")}
            m["feedback"] = fb if fb["rating"] is not None else None
            by_conv.setdefault(m.pop("conversation_id"), []).append(m)
        return {
            "account": found[0],
            "watchlist": rows(
                """SELECT w.corp_code, c.corp_name, c.stock_code, w.min_importance, w.added_at
                   FROM user_watchlist w LEFT JOIN companies c USING (corp_code)
                   WHERE w.user_id = %s ORDER BY w.added_at, w.corp_code""",
                (user_id,),
            ),
            "alert_channels": rows(
                """SELECT kind, target, verified_at, enabled, created_at
                   FROM user_alert_channels WHERE user_id = %s ORDER BY kind""",
                (user_id,),
            ),
            "notifications": rows(
                """SELECT rcept_no, channel, sent_at FROM user_notifications
                   WHERE user_id = %s ORDER BY sent_at, rcept_no""",
                (user_id,),
            ),
            # 기기 기록은 해시뿐이라 언제 처음·마지막으로 썼는지만 넣는다
            "devices": rows(
                """SELECT first_seen, last_seen FROM user_devices
                   WHERE user_id = %s ORDER BY first_seen""",
                (user_id,),
            ),
            # 웹 푸시 구독: 주소와 키는 그 브라우저로 보낼 수 있는 값이라
            # 푸시 서비스 호스트만 넣는다
            "push_subscriptions": rows(
                """SELECT label, substring(endpoint from '^https://([^/:]+)') AS push_service,
                          created_at, last_sent_at
                   FROM user_push_subscriptions WHERE user_id = %s ORDER BY created_at, id""",
                (user_id,),
            ),
            "two_factor": self._two_factor_export(user_id),
            "conversations": [c | {"messages": by_conv.get(c["id"], [])} for c in conversations],
        }

    def _two_factor_export(self, user_id: int) -> dict:
        row = self.conn.execute(
            """SELECT t.enabled_at,
                      (SELECT count(*) FROM user_recovery_codes r WHERE r.user_id = t.user_id)
               FROM user_totp t WHERE t.user_id = %s AND t.enabled_at IS NOT NULL""",
            (user_id,),
        ).fetchone()
        if row is None:
            return {"enabled": False}
        return {"enabled": True, "enabled_at": row[0], "recovery_codes_left": row[1]}

    def create_session(self, token_hash: str, user_id: int, expires_at) -> None:
        self.conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
            (token_hash, user_id, expires_at),
        )
        self.conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,))
        self.conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        self.conn.commit()

    def session_user(self, token_hash: str) -> tuple[int, str | None, object] | None:
        """(사용자 id, 이메일, 체험 계정이 끝나는 시각). 가입한 계정이면 끝나는 시각은 None,
        체험 계정이면 이메일이 None. 기한이 지난 체험 계정은 지우기 전이라도 로그인되지 않는다."""
        return self.conn.execute(
            """SELECT u.id, u.email, u.guest_expires_at
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = %s AND s.expires_at > now()
                 AND (u.guest_expires_at IS NULL OR u.guest_expires_at > now())""",
            (token_hash,),
        ).fetchone()

    def delete_session(self, token_hash: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))
        self.conn.commit()

    def revoke_sessions(self, user_id: int) -> int:
        """그 사용자의 모든 로그인을 끊는다."""
        cur = self.conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        self.conn.commit()
        return cur.rowcount

    # --- 계정 메일 (이메일 인증, 비밀번호 재설정, 새 기기 알림) --------------

    def email_verified(self, user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT email_verified_at IS NOT NULL FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        return bool(row and row[0])

    def mark_email_verified(self, user_id: int) -> None:
        self.conn.execute(
            """UPDATE users SET email_verified_at = now()
               WHERE id = %s AND email_verified_at IS NULL""",
            (user_id,),
        )
        self.conn.commit()

    def create_auth_token(self, user_id: int, purpose: str, token_hash: str, expires_at) -> None:
        """메일 링크용 토큰. 같은 용도의 예전 링크는 지워서 마지막 링크만 쓸 수 있게 한다."""
        self.conn.execute("DELETE FROM auth_tokens WHERE expires_at < now()")
        self.conn.execute(
            "DELETE FROM auth_tokens WHERE user_id = %s AND purpose = %s", (user_id, purpose)
        )
        self.conn.execute(
            """INSERT INTO auth_tokens (token_hash, user_id, purpose, expires_at)
               VALUES (%s, %s, %s, %s)""",
            (token_hash, user_id, purpose, expires_at),
        )
        self.conn.commit()

    def consume_auth_token(self, purpose: str, token_hash: str) -> tuple[int, str] | None:
        """토큰이 맞고 기한 안이면 (사용자 id, 이메일). 만료됐어도 지워서 한 번만 쓰게 한다."""
        row = self.conn.execute(
            """DELETE FROM auth_tokens t USING users u
               WHERE t.user_id = u.id AND t.token_hash = %s AND t.purpose = %s
               RETURNING t.user_id, u.email, t.expires_at > now()""",
            (token_hash, purpose),
        ).fetchone()
        self.conn.commit()
        return (row[0], row[1]) if row and row[2] else None

    def reset_password(self, user_id: int, password_hash: str) -> None:
        """메일 링크로 비밀번호 재설정. 메일을 받았으니 이메일도 인증된 것으로 본다.

        모든 세션과 남은 재설정 링크를 한 트랜잭션에서 지운다."""
        self.conn.execute(
            """UPDATE users SET password_hash = %s,
                 email_verified_at = COALESCE(email_verified_at, now())
               WHERE id = %s""",
            (password_hash, user_id),
        )
        self.conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        self.conn.execute(
            "DELETE FROM auth_tokens WHERE user_id = %s AND purpose = 'reset'", (user_id,)
        )
        self.conn.commit()

    def remember_device(self, user_id: int, device_hash: str) -> str:
        """로그인한 기기를 기록한다. 'first'(기록된 기기가 하나도 없었음), 'new', 'known'.

        1년 동안 쓰지 않은 기기 기록은 지운다."""
        self.conn.execute(
            """DELETE FROM user_devices
               WHERE user_id = %s AND last_seen < now() - interval '365 days'""",
            (user_id,),
        )
        had_any = self.conn.execute(
            "SELECT EXISTS (SELECT 1 FROM user_devices WHERE user_id = %s)", (user_id,)
        ).fetchone()[0]
        inserted = self.conn.execute(
            """INSERT INTO user_devices (user_id, device_hash) VALUES (%s, %s)
               ON CONFLICT (user_id, device_hash) DO UPDATE SET last_seen = now()
               RETURNING (xmax = 0)""",
            (user_id, device_hash),
        ).fetchone()[0]
        self.conn.commit()
        if not had_any:
            return "first"
        return "new" if inserted else "known"

    # --- 2단계 인증 (TOTP) ------------------------------------------------

    def totp(self, user_id: int) -> dict | None:
        """{secret(암호화된 값), enabled, last_step, recovery_left}. 등록한 적 없으면 None."""
        row = self.conn.execute(
            """SELECT t.secret, t.enabled_at IS NOT NULL, t.last_step,
                      (SELECT count(*) FROM user_recovery_codes r WHERE r.user_id = t.user_id)
               FROM user_totp t WHERE t.user_id = %s""",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {"secret": row[0], "enabled": row[1], "last_step": row[2], "recovery_left": row[3]}

    def totp_enabled(self, user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT enabled_at IS NOT NULL FROM user_totp WHERE user_id = %s", (user_id,)
        ).fetchone()
        return bool(row and row[0])

    def start_totp(self, user_id: int, sealed_secret: str) -> bool:
        """등록 시작: 새 비밀값을 '확인 전' 상태로 둔다. 이미 켜져 있으면 False (바꾸지 않음)."""
        row = self.conn.execute(
            """INSERT INTO user_totp (user_id, secret) VALUES (%s, %s)
               ON CONFLICT (user_id) DO UPDATE SET secret = EXCLUDED.secret,
                 last_step = NULL, created_at = now()
               WHERE user_totp.enabled_at IS NULL
               RETURNING user_id""",
            (user_id, sealed_secret),
        ).fetchone()
        self.conn.commit()
        return row is not None

    def enable_totp(self, user_id: int, step: int, recovery_hashes: list[str]) -> bool:
        """첫 코드를 확인하면 켠다. 복구 코드를 새로 두고, 다른 기기의 로그인을 모두 끊는다.

        한 트랜잭션에서 한다. 이미 켜져 있거나 등록을 시작하지 않았으면 False."""
        try:
            cur = self.conn.execute(
                """UPDATE user_totp SET enabled_at = now(), last_step = %s
                   WHERE user_id = %s AND enabled_at IS NULL""",
                (step, user_id),
            )
            if cur.rowcount == 0:
                self.conn.rollback()
                return False
            self._put_recovery_codes(user_id, recovery_hashes)
            self.conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return True

    def _put_recovery_codes(self, user_id: int, hashes: list[str]) -> None:
        self.conn.execute("DELETE FROM user_recovery_codes WHERE user_id = %s", (user_id,))
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO user_recovery_codes (user_id, code_hash) VALUES (%s, %s)",
                [(user_id, h) for h in hashes],
            )

    def replace_recovery_codes(self, user_id: int, hashes: list[str]) -> None:
        """복구 코드 새로 받기: 남은 예전 코드는 모두 못 쓰게 된다."""
        self._put_recovery_codes(user_id, hashes)
        self.conn.commit()

    def use_totp_step(self, user_id: int, step: int) -> bool:
        """코드의 시간 구간을 쓴 것으로 적는다. 이미 그 구간이나 뒤의 코드를 썼으면 False
        (같은 코드를 다시 쓰지 못하게, RFC 6238 5.2)."""
        cur = self.conn.execute(
            """UPDATE user_totp SET last_step = %s
               WHERE user_id = %s AND enabled_at IS NOT NULL
                 AND (last_step IS NULL OR last_step < %s)""",
            (step, user_id, step),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def use_recovery_code(self, user_id: int, code_hash: str) -> int | None:
        """복구 코드를 한 번 쓰고 지운다. 남은 수, 맞는 코드가 없으면 None."""
        cur = self.conn.execute(
            "DELETE FROM user_recovery_codes WHERE user_id = %s AND code_hash = %s",
            (user_id, code_hash),
        )
        left = self.conn.execute(
            "SELECT count(*) FROM user_recovery_codes WHERE user_id = %s", (user_id,)
        ).fetchone()[0]
        self.conn.commit()
        return left if cur.rowcount else None

    def disable_totp(self, user_id: int) -> bool:
        """2단계 인증 끄기: 비밀값, 복구 코드, 진행 중인 로그인 단계를 모두 지운다."""
        self.conn.execute("DELETE FROM user_recovery_codes WHERE user_id = %s", (user_id,))
        self.conn.execute("DELETE FROM login_challenges WHERE user_id = %s", (user_id,))
        cur = self.conn.execute("DELETE FROM user_totp WHERE user_id = %s", (user_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def create_login_challenge(self, token_hash: str, user_id: int, expires_at) -> None:
        """비밀번호는 맞고 인증 코드를 기다리는 로그인. 쿠키 토큰의 해시만 둔다."""
        self.conn.execute("DELETE FROM login_challenges WHERE expires_at < now()")
        self.conn.execute(
            """INSERT INTO login_challenges (token_hash, user_id, expires_at)
               VALUES (%s, %s, %s)""",
            (token_hash, user_id, expires_at),
        )
        self.conn.commit()

    def login_challenge(self, token_hash: str, max_attempts: int) -> tuple[int, str] | None:
        """기한 안이고 틀린 횟수가 남은 로그인 단계면 (사용자 id, 이메일)."""
        return self.conn.execute(
            """SELECT u.id, u.email FROM login_challenges c JOIN users u ON u.id = c.user_id
               WHERE c.token_hash = %s AND c.expires_at > now() AND c.attempts < %s""",
            (token_hash, max_attempts),
        ).fetchone()

    def fail_login_challenge(self, token_hash: str, max_attempts: int) -> int:
        """틀린 횟수를 하나 올리고 남은 횟수를 돌려준다. 다 쓰면 그 단계를 지운다."""
        row = self.conn.execute(
            """UPDATE login_challenges SET attempts = attempts + 1
               WHERE token_hash = %s RETURNING attempts""",
            (token_hash,),
        ).fetchone()
        left = max_attempts - row[0] if row else 0
        if left <= 0:
            self.conn.execute("DELETE FROM login_challenges WHERE token_hash = %s", (token_hash,))
        self.conn.commit()
        return max(left, 0)

    def delete_login_challenge(self, token_hash: str) -> None:
        self.conn.execute("DELETE FROM login_challenges WHERE token_hash = %s", (token_hash,))
        self.conn.commit()

    def pending_alerts(self, channel: str) -> list[dict]:
        """관심 종목의 기준 이상 공시 중 이 채널로 아직 안 보낸 것.

        정기보고서는 변경점 요약을 붙이려고 처리가 끝날 때까지(최대 2시간) 기다린다."""
        cur = self.conn.execute(
            """
            SELECT d.rcept_no, d.corp_name, d.report_nm, d.rcept_dt, d.event_label,
                   d.importance, d.correction
            FROM disclosures d
            JOIN watchlist w ON w.corp_code = d.corp_code AND d.importance >= w.min_importance
            WHERE d.seen_at >= w.added_at
              AND NOT (d.pblntf_ty = 'A' AND d.ingested_at IS NULL AND d.ingest_attempts < 5
                       AND d.seen_at > now() - interval '2 hours')
              AND NOT EXISTS (SELECT 1 FROM notifications n
                              WHERE n.rcept_no = d.rcept_no AND n.channel = %s)
            ORDER BY d.rcept_dt, d.rcept_no
            """,
            (channel,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def mark_notified(self, rcept_no: str, channel: str) -> None:
        self.conn.execute(
            "INSERT INTO notifications (rcept_no, channel) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (rcept_no, channel),
        )
        self.conn.commit()

    # --- 대화 기록 -------------------------------------------------------
    # user_id 가 None 이면 로그인 없이 쓰는 운영자 본인의 대화

    def create_conversation(self, user_id: int | None, title: str) -> int:
        row = self.conn.execute(
            "INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id",
            (user_id, title[:100]),
        ).fetchone()
        self.conn.commit()
        return row[0]

    def owns_conversation(self, conversation_id: int, user_id: int | None) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND user_id IS NOT DISTINCT FROM %s",
            (conversation_id, user_id),
        ).fetchone()
        return row is not None

    def add_message(self, conversation_id: int, role: str, content: str, payload: dict) -> int:
        from psycopg.types.json import Jsonb

        row = self.conn.execute(
            """INSERT INTO messages (conversation_id, role, content, payload)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (conversation_id, role, content, Jsonb(payload)),
        ).fetchone()
        self.conn.execute(
            "UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,)
        )
        self.conn.commit()
        return row[0]

    def conversations(self, user_id: int | None, limit: int = 50) -> list[dict]:
        cur = self.conn.execute(
            """SELECT id, title, created_at, updated_at FROM conversations
               WHERE user_id IS NOT DISTINCT FROM %s ORDER BY updated_at DESC LIMIT %s""",
            (user_id, limit),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def messages(self, conversation_id: int) -> list[dict]:
        cur = self.conn.execute(
            """SELECT m.id, m.role, m.content, m.payload, m.created_at,
                      f.rating, f.reason
               FROM messages m LEFT JOIN feedback f ON f.message_id = m.id
               WHERE m.conversation_id = %s ORDER BY m.id""",
            (conversation_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def last_context(self, conversation_id: int) -> dict | None:
        row = self.conn.execute(
            """SELECT payload->'context' FROM messages
               WHERE conversation_id = %s AND role = 'user' ORDER BY id DESC LIMIT 1""",
            (conversation_id,),
        ).fetchone()
        return row[0] if row else None

    def delete_conversation(self, conversation_id: int, user_id: int | None) -> bool:
        cur = self.conn.execute(
            "DELETE FROM conversations WHERE id = %s AND user_id IS NOT DISTINCT FROM %s",
            (conversation_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def purge_conversations(self, older_than_days: int) -> int:
        """보관 기간이 지난 대화 삭제."""
        cur = self.conn.execute(
            "DELETE FROM conversations WHERE updated_at < now() - make_interval(days => %s)",
            (older_than_days,),
        )
        self.conn.commit()
        return cur.rowcount

    def set_feedback(
        self,
        message_id: int,
        user_id: int | None,
        rating: int,
        reason: str | None,
        comment: str | None,
    ) -> bool:
        """자기 대화의 답변에만 평가를 남길 수 있다."""
        owned = self.conn.execute(
            """SELECT 1 FROM messages m JOIN conversations c ON c.id = m.conversation_id
               WHERE m.id = %s AND m.role = 'assistant'
                 AND c.user_id IS NOT DISTINCT FROM %s""",
            (message_id, user_id),
        ).fetchone()
        if not owned:
            return False
        self.conn.execute(
            """INSERT INTO feedback (message_id, rating, reason, comment)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (message_id) DO UPDATE SET rating = EXCLUDED.rating,
                 reason = EXCLUDED.reason, comment = EXCLUDED.comment, created_at = now()""",
            (message_id, rating, reason, comment),
        )
        self.conn.commit()
        return True

    def feedback_rows(self, rating: int | None = None) -> list[dict]:
        """평가와 그 답변, 바로 앞 질문 (평가셋 편입용)."""
        query = """
            SELECT f.message_id, f.rating, f.reason, f.comment, f.created_at,
                   a.content AS answer, a.payload AS answer_payload,
                   q.content AS question, q.payload AS question_payload
            FROM feedback f
            JOIN messages a ON a.id = f.message_id
            JOIN LATERAL (
                SELECT content, payload FROM messages
                WHERE conversation_id = a.conversation_id AND role = 'user' AND id < a.id
                ORDER BY id DESC LIMIT 1
            ) q ON true
        """
        params: tuple = ()
        if rating is not None:
            query += " WHERE f.rating = %s"
            params = (rating,)
        cur = self.conn.execute(query + " ORDER BY f.created_at", params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    # --- 변경점 요약 -----------------------------------------------------

    def diff_summary(self, old_rcept_no: str, new_rcept_no: str) -> dict | None:
        row = self.conn.execute(
            """SELECT version, model, payload, created_at FROM diff_summaries
               WHERE old_rcept_no = %s AND new_rcept_no = %s""",
            (old_rcept_no, new_rcept_no),
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("version", "model", "payload", "created_at"), row, strict=True))

    def latest_diff_summary_for(self, new_rcept_no: str) -> dict | None:
        """이 공시를 '이후' 보고서로 만든 요약 (알림에 붙인다)."""
        row = self.conn.execute(
            """SELECT payload FROM diff_summaries WHERE new_rcept_no = %s
               ORDER BY created_at DESC LIMIT 1""",
            (new_rcept_no,),
        ).fetchone()
        return row[0] if row else None

    def save_diff_summary(
        self, old_rcept_no: str, new_rcept_no: str, version: int, model: str | None, payload: dict
    ) -> None:
        import json

        from psycopg.types.json import Jsonb

        # 공시 정보에 날짜가 들어 있어 문자열로 바꿔 저장한다
        data = Jsonb(json.loads(json.dumps(payload, default=str)))
        self.conn.execute(
            """INSERT INTO diff_summaries (old_rcept_no, new_rcept_no, version, model, payload)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (old_rcept_no, new_rcept_no) DO UPDATE SET
                 version = EXCLUDED.version, model = EXCLUDED.model,
                 payload = EXCLUDED.payload, created_at = now()""",
            (old_rcept_no, new_rcept_no, version, model, data),
        )
        self.conn.commit()

    # --- 사용자별 알림 채널 ----------------------------------------------

    def start_alert_channel(
        self, user_id: int, kind: str, target: str | None, pending_hash: str, until
    ) -> None:
        """인증을 새로 시작한다. 인증이 끝날 때까지 이 채널로는 보내지 않는다."""
        self.conn.execute(
            """INSERT INTO user_alert_channels
                 (user_id, kind, target, verified_at, pending_hash, pending_until)
               VALUES (%s, %s, %s, NULL, %s, %s)
               ON CONFLICT (user_id, kind) DO UPDATE SET
                 target = EXCLUDED.target, verified_at = NULL, enabled = true,
                 pending_hash = EXCLUDED.pending_hash, pending_until = EXCLUDED.pending_until""",
            (user_id, kind, target, pending_hash, until),
        )
        self.conn.commit()

    def confirm_alert_channel(
        self, kind: str, pending_hash: str, target: str | None = None
    ) -> int | None:
        """인증 코드가 맞고 기한 안이면 채널을 켠다. target 이 있으면 그 값으로 바꾼다."""
        row = self.conn.execute(
            """UPDATE user_alert_channels SET
                 verified_at = now(), pending_hash = NULL, pending_until = NULL,
                 target = COALESCE(%s, target), enabled = true
               WHERE kind = %s AND pending_hash = %s AND pending_until > now()
               RETURNING user_id""",
            (target, kind, pending_hash),
        ).fetchone()
        self.conn.commit()
        return row[0] if row else None

    def alert_channels(self, user_id: int) -> list[dict]:
        cur = self.conn.execute(
            """SELECT kind, target, verified_at IS NOT NULL AS verified, enabled,
                      pending_until > now() AS pending
               FROM user_alert_channels WHERE user_id = %s ORDER BY kind""",
            (user_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def set_alert_enabled(self, user_id: int, kind: str, enabled: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE user_alert_channels SET enabled = %s WHERE user_id = %s AND kind = %s",
            (enabled, user_id, kind),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def disable_telegram_chat(self, chat_id: str) -> int:
        cur = self.conn.execute(
            """UPDATE user_alert_channels SET enabled = false
               WHERE kind = 'telegram' AND target = %s""",
            (chat_id,),
        )
        self.conn.commit()
        return cur.rowcount

    def remove_alert_channel(self, user_id: int, kind: str) -> bool:
        if kind == "push":  # 웹 푸시를 지우면 모든 브라우저의 구독도 지운다
            self.conn.execute("DELETE FROM user_push_subscriptions WHERE user_id = %s", (user_id,))
        cur = self.conn.execute(
            "DELETE FROM user_alert_channels WHERE user_id = %s AND kind = %s", (user_id, kind)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- 웹 푸시 구독 (브라우저마다 한 줄) ---------------------------------

    def add_push_subscription(
        self,
        user_id: int,
        endpoint: str,
        p256dh: str,
        auth: str,
        vapid_key: str,
        label: str,
        max_per_user: int = 10,
    ) -> int:
        """구독을 저장하고 웹 푸시 채널을 켠다. 구독 id.

        같은 브라우저(같은 주소)를 다른 사용자가 구독하면 그 사용자 것으로 옮긴다 (마지막에 구독한
        사람에게만 간다). 한 사용자의 구독이 max_per_user 개를 넘으면 오래된 것부터 지운다."""
        try:
            prev = self.conn.execute(
                "SELECT user_id FROM user_push_subscriptions WHERE endpoint = %s", (endpoint,)
            ).fetchone()
            sub_id = self.conn.execute(
                """INSERT INTO user_push_subscriptions
                     (user_id, endpoint, p256dh, auth, vapid_key, label)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (endpoint) DO UPDATE SET
                     user_id = EXCLUDED.user_id, p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                     vapid_key = EXCLUDED.vapid_key, label = EXCLUDED.label,
                     created_at = now(), last_sent_at = NULL
                   RETURNING id""",
                (user_id, endpoint, p256dh, auth, vapid_key, label),
            ).fetchone()[0]
            self.conn.execute(
                """DELETE FROM user_push_subscriptions WHERE user_id = %s AND id NOT IN (
                     SELECT id FROM user_push_subscriptions WHERE user_id = %s
                     ORDER BY created_at DESC, id DESC LIMIT %s)""",
                (user_id, user_id, max_per_user),
            )
            # 구독은 로그인한 사용자의 브라우저가 직접 만든 것이라 따로 인증하지 않는다
            self.conn.execute(
                """INSERT INTO user_alert_channels (user_id, kind, target, verified_at, enabled)
                   VALUES (%s, 'push', NULL, now(), true)
                   ON CONFLICT (user_id, kind) DO UPDATE SET enabled = true,
                     verified_at = COALESCE(user_alert_channels.verified_at, now())""",
                (user_id,),
            )
            if prev and prev[0] != user_id:
                self._drop_empty_push_channel(prev[0])
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return sub_id

    def _drop_empty_push_channel(self, user_id: int) -> None:
        """구독이 하나도 남지 않으면 웹 푸시 채널도 지운다 (커밋은 부르는 쪽이 한다)."""
        self.conn.execute(
            """DELETE FROM user_alert_channels c WHERE c.user_id = %s AND c.kind = 'push'
                 AND NOT EXISTS (SELECT 1 FROM user_push_subscriptions s
                                 WHERE s.user_id = c.user_id)""",
            (user_id,),
        )

    def push_subscriptions(self, user_id: int) -> list[dict]:
        """보낼 때 쓰는 구독 정보 (주소와 키)."""
        cur = self.conn.execute(
            """SELECT id, endpoint, p256dh, auth, vapid_key FROM user_push_subscriptions
               WHERE user_id = %s ORDER BY id""",
            (user_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def push_devices(self, user_id: int) -> list[dict]:
        """화면용 목록. 주소 대신 주소의 해시(key)를 줘서 화면이 '이 브라우저'를 알아보게 한다."""
        cur = self.conn.execute(
            """SELECT id, label, encode(sha256(convert_to(endpoint, 'UTF8')), 'hex') AS key,
                      created_at, last_sent_at
               FROM user_push_subscriptions WHERE user_id = %s ORDER BY created_at, id""",
            (user_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def remove_push_subscription(
        self, user_id: int, *, endpoint: str | None = None, sub_id: int | None = None
    ) -> bool:
        """그 사용자의 구독 하나를 지운다 (주소나 id 로). 마지막 구독이면 채널도 지운다."""
        cur = self.conn.execute(
            """DELETE FROM user_push_subscriptions
               WHERE user_id = %s AND (endpoint = %s OR id = %s)""",
            (user_id, endpoint, sub_id),
        )
        self._drop_empty_push_channel(user_id)
        self.conn.commit()
        return cur.rowcount > 0

    def record_push_results(self, user_id: int, sent: list[int], gone: list[int]) -> None:
        """보낸 구독은 마지막 전송 시각을 적고, 끝난 구독은 지운다."""
        if sent:
            self.conn.execute(
                """UPDATE user_push_subscriptions SET last_sent_at = now()
                   WHERE user_id = %s AND id = ANY(%s)""",
                (user_id, sent),
            )
        if gone:
            self.conn.execute(
                "DELETE FROM user_push_subscriptions WHERE user_id = %s AND id = ANY(%s)",
                (user_id, gone),
            )
            self._drop_empty_push_channel(user_id)
        self.conn.commit()

    def user_pending_alerts(self, max_age_days: int = 3) -> list[dict]:
        """인증된 채널이 있는 사용자의 관심 종목 공시 중 아직 안 보낸 것.

        채널을 켠 직후 예전 공시가 한꺼번에 가지 않게 최근 며칠 것만 본다."""
        cur = self.conn.execute(
            """
            SELECT ch.user_id, ch.kind, ch.target, d.rcept_no, d.corp_code, d.corp_name,
                   d.report_nm, d.rcept_dt, d.event_label, d.importance, d.correction
            FROM user_alert_channels ch
            JOIN user_watchlist w ON w.user_id = ch.user_id
            JOIN disclosures d ON d.corp_code = w.corp_code AND d.importance >= w.min_importance
            WHERE ch.enabled AND ch.verified_at IS NOT NULL
              -- 웹 푸시는 받는 곳이 구독 표(브라우저마다 한 줄)에 있다
              AND (ch.target IS NOT NULL OR ch.kind = 'push')
              AND (ch.kind <> 'push' OR EXISTS (
                     SELECT 1 FROM user_push_subscriptions s WHERE s.user_id = ch.user_id))
              AND d.seen_at >= w.added_at AND d.seen_at >= ch.verified_at - interval '1 day'
              AND d.seen_at >= now() - make_interval(days => %s)
              -- 정기보고서는 처리(변경점 요약)가 끝나면 보낸다. 2시간이 지나면 그냥 보낸다
              AND NOT (d.pblntf_ty = 'A' AND d.ingested_at IS NULL AND d.ingest_attempts < 5
                       AND d.seen_at > now() - interval '2 hours')
              AND NOT EXISTS (SELECT 1 FROM user_notifications n
                              WHERE n.user_id = ch.user_id AND n.rcept_no = d.rcept_no
                                AND n.channel = ch.kind)
            ORDER BY ch.user_id, ch.kind, d.importance DESC, d.rcept_dt, d.rcept_no
            """,
            (max_age_days,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def mark_user_notified(self, user_id: int, rcept_nos: list[str], channel: str) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO user_notifications (user_id, rcept_no, channel)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                [(user_id, r, channel) for r in rcept_nos],
            )
        self.conn.commit()

    # --- 새 정기보고서 처리 대기열 ---------------------------------------

    def periodic_to_ingest(self, limit: int = 20, max_attempts: int = 5) -> list[dict]:
        """피드로 받은 정기보고서 중 아직 처리하지 않은 것 (우리 DB 의 상장사만)."""
        cur = self.conn.execute(
            """SELECT d.rcept_no, d.corp_code, d.corp_name, d.report_nm, d.rcept_dt,
                      d.ingest_attempts
               FROM disclosures d JOIN companies c USING (corp_code)
               WHERE d.pblntf_ty = 'A' AND d.ingested_at IS NULL AND d.ingest_attempts < %s
               ORDER BY d.seen_at, d.rcept_no LIMIT %s""",
            (max_attempts, limit),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def mark_ingested(self, rcept_no: str) -> None:
        self.conn.execute(
            """UPDATE disclosures SET ingested_at = now(), ingest_error = NULL,
                 ingest_attempts = ingest_attempts + 1 WHERE rcept_no = %s""",
            (rcept_no,),
        )
        self.conn.commit()

    def mark_ingest_failed(self, rcept_no: str, error: str) -> None:
        self.conn.execute(
            """UPDATE disclosures SET ingest_error = %s, ingest_attempts = ingest_attempts + 1
               WHERE rcept_no = %s""",
            (error[:500], rcept_no),
        )
        self.conn.commit()

    def ingest_backlog(self) -> dict:
        row = self.conn.execute(
            """SELECT count(*) FILTER (WHERE ingested_at IS NULL AND ingest_attempts < 5),
                      count(*) FILTER (WHERE ingested_at IS NULL AND ingest_attempts >= 5),
                      min(seen_at) FILTER (WHERE ingested_at IS NULL AND ingest_attempts < 5)
               FROM disclosures JOIN companies USING (corp_code) WHERE pblntf_ty = 'A'"""
        ).fetchone()
        return {"pending": row[0], "failed": row[1], "oldest_pending": row[2]}

    # --- 과거 데이터 채우기 ----------------------------------------------

    def plan_backfill(self, start_year: int, end_year: int) -> int:
        """아직 계획에 없는 상장사를 대기 상태로 넣는다. 이미 있는 회사는 기간만 넓힌다."""
        cur = self.conn.execute(
            """INSERT INTO backfill_state (corp_code, start_year, end_year)
               SELECT corp_code, %s, %s FROM companies WHERE stock_code IS NOT NULL
               ON CONFLICT (corp_code) DO UPDATE SET
                 start_year = LEAST(backfill_state.start_year, EXCLUDED.start_year),
                 end_year = GREATEST(backfill_state.end_year, EXCLUDED.end_year),
                 status = CASE WHEN EXCLUDED.start_year < backfill_state.start_year
                                 OR EXCLUDED.end_year > backfill_state.end_year
                               THEN 'pending' ELSE backfill_state.status END
               RETURNING (xmax = 0)""",
            (start_year, end_year),
        )
        added = sum(1 for (inserted,) in cur.fetchall() if inserted)
        self.conn.commit()
        return added

    def next_backfill(self, limit: int, max_attempts: int = 3) -> list[tuple[str, int, int]]:
        return self.conn.execute(
            """SELECT corp_code, start_year, end_year FROM backfill_state
               WHERE status = 'pending' OR (status = 'error' AND attempts < %s)
               ORDER BY attempts, corp_code LIMIT %s""",
            (max_attempts, limit),
        ).fetchall()

    def finish_backfill(self, corp_code: str, filings: int) -> None:
        self.conn.execute(
            """UPDATE backfill_state SET status = 'done', filings = %s, last_error = NULL,
                 attempts = attempts + 1, updated_at = now() WHERE corp_code = %s""",
            (filings, corp_code),
        )
        self.conn.commit()

    def fail_backfill(self, corp_code: str, error: str) -> None:
        self.conn.execute(
            """UPDATE backfill_state SET status = 'error', last_error = %s,
                 attempts = attempts + 1, updated_at = now() WHERE corp_code = %s""",
            (error[:500], corp_code),
        )
        self.conn.commit()

    def backfill_progress(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, count(*) FROM backfill_state GROUP BY status"
        ).fetchall()
        return {"pending": 0, "done": 0, "error": 0} | dict(rows)

    # --- 작업 실행 기록 --------------------------------------------------

    def start_job(self, name: str) -> int:
        row = self.conn.execute(
            "INSERT INTO job_runs (name) VALUES (%s) RETURNING id", (name,)
        ).fetchone()
        self.conn.commit()
        return row[0]

    def finish_job(self, job_id: int, status: str, detail: dict | None = None) -> None:
        import json

        from psycopg.types.json import Jsonb

        data = Jsonb(json.loads(json.dumps(detail or {}, default=str)))
        self.conn.execute(
            "UPDATE job_runs SET status = %s, detail = %s, finished_at = now() WHERE id = %s",
            (status, data, job_id),
        )
        self.conn.commit()

    def recent_jobs(self, limit: int = 50) -> list[dict]:
        cur = self.conn.execute(
            """SELECT id, name, status, started_at, finished_at, detail FROM job_runs
               ORDER BY started_at DESC LIMIT %s""",
            (limit,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def last_job_runs(self) -> dict[str, dict]:
        """작업 이름별 마지막 실행과 마지막 성공 시각 (상태 점검용)."""
        cur = self.conn.execute(
            """SELECT name,
                      max(started_at) AS last_started,
                      max(finished_at) FILTER (WHERE status = 'ok') AS last_ok,
                      (array_agg(status ORDER BY started_at DESC))[1] AS last_status
               FROM job_runs GROUP BY name"""
        )
        cols = [c.name for c in cur.description]
        return {r[0]: dict(zip(cols[1:], r[1:], strict=True)) for r in cur.fetchall()}

    # --- 운영 지표 ---------------------------------------------------------

    def ops_snapshot(self) -> dict:
        """Prometheus 지표용 현재 상태 (작업, 처리 대기, 데이터 오류, 사용자, 최근 평가)."""
        jobs = [
            {"name": name, "last_success": r["last_ok"], "status": r["last_status"]}
            for name, r in self.last_job_runs().items()
        ]
        issues = dict(
            self.conn.execute(
                "SELECT severity, count(*) FROM data_issues GROUP BY severity"
            ).fetchall()
        )
        users, guests = self.conn.execute(
            """SELECT count(*) FILTER (WHERE guest_expires_at IS NULL),
                      count(*) FILTER (WHERE guest_expires_at > now())
               FROM users"""
        ).fetchone()
        out = {
            "jobs": jobs,
            "ingest_backlog": self.ingest_backlog()["pending"],
            "issues": {"error": issues.get("error", 0), "warn": issues.get("warn", 0)},
            "users": users,
            "guests": guests,
        }
        if latest := self.latest_eval_run():
            out["eval"] = latest
        return out

    def save_eval_run(self, meta: dict, summary: dict, passed: bool) -> int:
        from psycopg.types.json import Jsonb

        row = self.conn.execute(
            "INSERT INTO eval_runs (meta, summary, passed) VALUES (%s, %s, %s) RETURNING id",
            (Jsonb(meta), Jsonb(summary), passed),
        ).fetchone()
        self.conn.commit()
        return row[0]

    def latest_eval_run(self) -> dict | None:
        row = self.conn.execute(
            """SELECT id, finished_at, meta, summary, passed FROM eval_runs
               ORDER BY finished_at DESC LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("id", "finished_at", "meta", "summary", "passed"), row, strict=True))

    def purge_job_runs(self, older_than_days: int) -> int:
        cur = self.conn.execute(
            "DELETE FROM job_runs WHERE started_at < now() - make_interval(days => %s)",
            (older_than_days,),
        )
        self.conn.commit()
        return cur.rowcount

    # --- 데이터 검증 ------------------------------------------------------

    def replace_issues(self, corp_code: str, issues) -> None:
        """검증 결과 교체. 해결된 문제는 지우고 계속되는 문제는 처음 본 시각을 지킨다."""
        issues = list(issues)
        with self.conn.transaction(), self.conn.cursor() as cur:
            before = cur.execute(
                """SELECT bsns_year, reprt_code, fs_div, rule, first_seen, severity, detail
                   FROM data_issues WHERE corp_code = %s""",
                (corp_code,),
            ).fetchall()
            first_seen = {tuple(r[:4]): r[4] for r in before}
            # 대시보드에 보이는 내용이 같으면 회사 버전을 그대로 둔다 (밤마다 전체 검증을 돌린다)
            shown = {(*r[:4], r[5], r[6]) for r in before}
            after = {
                (i.bsns_year, i.reprt_code, i.fs_div, i.rule, i.severity, i.detail) for i in issues
            }
            if shown != after:
                self._bump_company_versions(cur, [corp_code])
            cur.execute("DELETE FROM data_issues WHERE corp_code = %s", (corp_code,))
            cur.executemany(
                """INSERT INTO data_issues (corp_code, bsns_year, reprt_code, fs_div, rule,
                     severity, detail, first_seen)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))""",
                [
                    (
                        corp_code,
                        i.bsns_year,
                        i.reprt_code,
                        i.fs_div,
                        i.rule,
                        i.severity,
                        i.detail,
                        first_seen.get((i.bsns_year, i.reprt_code, i.fs_div, i.rule)),
                    )
                    for i in issues
                ],
            )

    def data_issues(self, corp_code: str | None = None, severity: str | None = None) -> list[dict]:
        query = """SELECT i.corp_code, c.corp_name, i.bsns_year, i.reprt_code, i.fs_div,
                          i.rule, i.severity, i.detail, i.first_seen, i.last_seen
                   FROM data_issues i LEFT JOIN companies c USING (corp_code) WHERE true"""
        params: list = []
        if corp_code:
            query += " AND i.corp_code = %s"
            params.append(corp_code)
        if severity:
            query += " AND i.severity = %s"
            params.append(severity)
        cur = self.conn.execute(
            query + " ORDER BY i.severity, i.corp_code, i.bsns_year DESC", params
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    # --- 보관 기간 정리 ---------------------------------------------------

    def purge_expired_sessions(self) -> int:
        cur = self.conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        self.conn.execute("DELETE FROM login_challenges WHERE expires_at < now()")
        self.conn.commit()
        return cur.rowcount

    def purge_user_notifications(self, older_than_days: int) -> int:
        cur = self.conn.execute(
            "DELETE FROM user_notifications WHERE sent_at < now() - make_interval(days => %s)",
            (older_than_days,),
        )
        self.conn.commit()
        return cur.rowcount
