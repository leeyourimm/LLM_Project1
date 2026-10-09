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
        return self.conn.execute(
            "SELECT id, email, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()

    def remove_user(self, email: str) -> bool:
        cur = self.conn.execute("DELETE FROM users WHERE email = %s", (email,))
        self.conn.commit()
        return cur.rowcount > 0

    def create_session(self, token_hash: str, user_id: int, expires_at) -> None:
        self.conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
            (token_hash, user_id, expires_at),
        )
        self.conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,))
        self.conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        self.conn.commit()

    def session_user(self, token_hash: str) -> tuple[int, str] | None:
        return self.conn.execute(
            """SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = %s AND s.expires_at > now()""",
            (token_hash,),
        ).fetchone()

    def delete_session(self, token_hash: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))
        self.conn.commit()

    def pending_alerts(self, channel: str) -> list[dict]:
        """관심 종목의 기준 이상 공시 중 이 채널로 아직 안 보낸 것."""
        cur = self.conn.execute(
            """
            SELECT d.rcept_no, d.corp_name, d.report_nm, d.rcept_dt, d.event_label,
                   d.importance, d.correction
            FROM disclosures d
            JOIN watchlist w ON w.corp_code = d.corp_code AND d.importance >= w.min_importance
            WHERE d.seen_at >= w.added_at
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
        cur = self.conn.execute(
            "DELETE FROM user_alert_channels WHERE user_id = %s AND kind = %s", (user_id, kind)
        )
        self.conn.commit()
        return cur.rowcount > 0

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
            WHERE ch.enabled AND ch.verified_at IS NOT NULL AND ch.target IS NOT NULL
              AND d.seen_at >= w.added_at AND d.seen_at >= ch.verified_at - interval '1 day'
              AND d.seen_at >= now() - make_interval(days => %s)
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
