"""웹 API 와 화면.

화면은 빌드 도구 없이 바로 열 수 있게 정적 HTML·JS 한 벌로 만들었다 (static/).
"""

import pathlib
from dataclasses import asdict
from datetime import date, timedelta
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dartrag.search import SearchFilter
from dartrag.web.services import Services

STATIC = pathlib.Path(__file__).parent / "static"
DISCLAIMER = "공시 정보 요약이며 투자 권유가 아닙니다. 중요한 판단은 원문을 확인하세요."


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    stocks: list[str] = Field(default_factory=list, max_length=5)
    year_from: int | None = None
    year_to: int | None = None


class WatchRequest(BaseModel):
    stock: str = Field(pattern=r"^\d{6}$")
    min_importance: int = Field(2, ge=1, le=3)


def _source(number: int | None, hit) -> dict:
    c = hit.chunk
    return {
        "number": number,
        "chunk_id": hit.chunk_id,
        "corp_name": c.get("corp_name"),
        "report_nm": c.get("report_nm"),
        "section": " > ".join(c.get("section_path", [])),
        "kind": c.get("kind"),
        "body": c.get("body", ""),
        "unit": c.get("unit"),
        "url": c.get("url"),
    }


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="DART 공시 분석", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def corp_codes(repo, stocks: list[str]) -> list[str]:
        if not stocks:
            return []
        codes = repo.corp_codes_for_stocks(stocks)
        if not codes:
            raise HTTPException(404, "해당 종목코드의 기업이 없습니다")
        return codes

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/companies")
    def companies():
        with services.repo() as repo:
            return [
                {"corp_code": c, "corp_name": n, "stock_code": s}
                for c, n, s in repo.listed_companies_with_stock()
            ]

    @app.post("/api/ask")
    def ask(req: AskRequest):
        from dartrag.answer import LLMError

        with services.repo() as repo:
            flt = SearchFilter(
                corp_codes=corp_codes(repo, req.stocks),
                year_from=req.year_from,
                year_to=req.year_to,
            )
            try:
                result = services.answerer(repo).answer(req.question, flt)
            except LLMError as e:
                raise HTTPException(503, str(e)) from None
        cited = {c.number for c in result.citations}
        return {
            "question": result.question,
            "answer": result.text,
            "found": result.found,
            "warnings": result.warnings,
            "unverified_numbers": result.unverified,
            "sources": [
                _source(i, h) | {"cited": i in cited} for i, h in enumerate(result.hits, start=1)
            ],
            "disclaimer": DISCLAIMER,
        }

    @app.get("/api/search")
    def search(
        q: Annotated[str, Query(min_length=2, max_length=500)],
        stocks: Annotated[list[str], Query()] = [],  # noqa: B006
        limit: Annotated[int, Query(ge=1, le=30)] = 10,
    ):
        with services.repo() as repo:
            flt = SearchFilter(corp_codes=corp_codes(repo, stocks))
            hits = services.retriever(repo).search(q, flt, limit)
        return [
            _source(i, h) | {"dense_rank": h.dense_rank, "keyword_rank": h.keyword_rank}
            for i, h in enumerate(hits, start=1)
        ]

    @app.get("/api/feed")
    def feed(
        days: Annotated[int, Query(ge=1, le=90)] = 7,
        min_importance: Annotated[int, Query(ge=1, le=3)] = 2,
        stocks: Annotated[list[str], Query()] = [],  # noqa: B006
        watched_only: bool = False,
    ):
        with services.repo() as repo:
            codes = corp_codes(repo, stocks)
            if watched_only:
                codes = [c for c, *_ in repo.watchlist()] or ["-"]
            rows = repo.recent_disclosures(
                date.today() - timedelta(days=days), min_importance, codes or None
            )
        return [
            r | {"url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}"}
            for r in rows
        ]

    @app.get("/api/watchlist")
    def watchlist():
        with services.repo() as repo:
            return [
                {"corp_code": c, "corp_name": n, "stock_code": s, "min_importance": m}
                for c, n, s, m in repo.watchlist()
            ]

    @app.post("/api/watchlist", status_code=201)
    def watch_add(req: WatchRequest):
        with services.repo() as repo:
            [code] = corp_codes(repo, [req.stock])
            repo.set_watch(code, req.min_importance)
        return {"ok": True}

    @app.delete("/api/watchlist/{stock}")
    def watch_remove(stock: str):
        with services.repo() as repo:
            [code] = corp_codes(repo, [stock])
            if not repo.remove_watch(code):
                raise HTTPException(404, "관심 종목에 없습니다")
        return {"ok": True}

    @app.get("/api/diff")
    def diff(
        stock: Annotated[str, Query(pattern=r"^\d{6}$")],
        kind: str = "사업보고서",
    ):
        from dartrag.changes.compare import compare_filings, latest_pair

        with services.repo() as repo:
            [code] = corp_codes(repo, [stock])
            pair = latest_pair(repo, code, kind)
            if pair is None:
                raise HTTPException(404, f"비교할 {kind}가 두 건 이상 없습니다")
            try:
                result = compare_filings(repo, *pair)
            except ValueError as e:
                raise HTTPException(404, str(e)) from None
        return {
            "title": result.title,
            "old": result.old,
            "new": result.new,
            "sections": [
                {
                    "key": d.key,
                    "status": d.status,
                    "importance": d.importance,
                    "added": d.added,
                    "removed": d.removed,
                    "modified": [{"before": a, "after": b} for a, b in d.modified],
                    "numbers_only": len(d.numbers_only),
                }
                for d in result.diffs
            ],
        }

    @app.get("/api/company/{stock}")
    def company(
        stock: Annotated[str, Path(pattern=r"^\d{6}$")],
        years: Annotated[int, Query(ge=2, le=10)] = 5,
        days: Annotated[int, Query(ge=1, le=365)] = 90,
    ):
        from dartrag.finance.series import company_series

        with services.repo() as repo:
            found = repo.company_by_stock(stock)
            if found is None:
                raise HTTPException(404, "해당 종목코드의 기업이 없습니다")
            code, name, stock_code = found
            series = company_series(repo, code, years)
            disclosures = repo.recent_disclosures(date.today() - timedelta(days=days), 1, [code])
            watched = any(c == code for c, *_ in repo.watchlist())
        return {
            "corp_code": code,
            "corp_name": name,
            "stock_code": stock_code,
            "watched": watched,
            "series": [asdict(p) for p in series],
            "disclosures": [
                d | {"url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d['rcept_no']}"}
                for d in disclosures[:30]
            ],
            "disclaimer": DISCLAIMER,
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
