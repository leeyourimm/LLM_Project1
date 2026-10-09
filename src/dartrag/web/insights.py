"""기업 분석 API: 분기 추이, 기업 비교, PDF 리포트."""

from dataclasses import asdict
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from dartrag.changes.summary import DART_URL
from dartrag.search import SearchFilter
from dartrag.web.chat import DISCLAIMER, _no_limit, source_dict

STOCK = r"^\d{6}$"
COMPARE_TOPICS = {
    "business": "주요 사업 부문과 매출 구성",
    "risk": "사업보고서에 적힌 주요 위험 요인",
    "investment": "연구개발과 설비 투자 계획",
}


class CompareSummaryRequest(BaseModel):
    stocks: list[Annotated[str, Field(pattern=STOCK)]] = Field(min_length=2, max_length=5)
    topic: str = Field("business", pattern="^(" + "|".join(COMPARE_TOPICS) + ")$")


def _flag(name: str):
    return lambda request: request.query_params.get(name, "").lower() in ("1", "true", "yes")


def build_router(services, rate=_no_limit) -> APIRouter:
    router = APIRouter()

    def company_or_404(repo, stock: str) -> tuple[str, str, str]:
        found = repo.company_by_stock(stock)
        if found is None:
            raise HTTPException(404, f"종목코드 {stock} 의 기업이 없습니다")
        return found

    def companies_or_404(repo, stocks: list[str]) -> list[tuple[str, str, str]]:
        unique = list(dict.fromkeys(stocks))
        if not 2 <= len(unique) <= 5:
            raise HTTPException(422, "서로 다른 기업 2~5곳을 골라 주세요")
        return [company_or_404(repo, s) for s in unique]

    @router.get("/api/company/{stock}/quarters")
    def quarters(
        stock: Annotated[str, Path(pattern=STOCK)],
        count: Annotated[int, Query(ge=4, le=40)] = 12,
    ):
        from dartrag.finance.series import quarterly_series

        with services.repo() as repo:
            code, name, stock_code = company_or_404(repo, stock)
            points = quarterly_series(repo, code, count)
        return {
            "corp_code": code,
            "corp_name": name,
            "stock_code": stock_code,
            "quarters": [asdict(p) | {"label": p.label} for p in points],
            "note": "4분기는 연간 금액에서 3분기 누적 금액을 빼서 계산합니다 (derived).",
        }

    @router.get("/api/compare")
    def compare(
        stocks: Annotated[list[str], Query()],
        years: Annotated[int, Query(ge=2, le=10)] = 5,
    ):
        from dartrag.finance.series import company_series, compare_table

        with services.repo() as repo:
            found = companies_or_404(repo, stocks)
            series = {code: company_series(repo, code, years) for code, *_ in found}
        table = compare_table(series)
        return {
            "year": table["year"],
            "companies": [
                {
                    "corp_code": code,
                    "corp_name": name,
                    "stock_code": stock_code,
                    "point": asdict(p) if (p := table["points"][code]) else None,
                    "series": [asdict(x) for x in series[code]],
                }
                for code, name, stock_code in found
            ],
            "disclaimer": DISCLAIMER,
        }

    @router.post("/api/compare/summary", dependencies=[rate("heavy")])
    def compare_summary(req: CompareSummaryRequest):
        """여러 회사의 공시 본문을 근거로 한 비교 설명 (답변 모델 사용)."""
        from dartrag.answer import LLMError

        with services.repo() as repo:
            found = companies_or_404(repo, req.stocks)
            names = ", ".join(name for _, name, _ in found)
            question = f"{names}의 {COMPARE_TOPICS[req.topic]}을 회사별로 비교해줘"
            flt = SearchFilter(corp_codes=[code for code, *_ in found])
            answerer = services.answerer(repo)
            try:
                result = answerer.answer(question, flt)
            except LLMError as e:
                raise HTTPException(503, str(e)) from None
        cited = {c.number for c in result.citations}
        return {
            "question": question,
            "answer": result.text,
            "found": result.found,
            "warnings": result.warnings,
            "sources": [
                source_dict(i, h) | {"cited": i in cited}
                for i, h in enumerate(result.hits, start=1)
            ],
            "model": answerer.llm.name,
            "disclaimer": DISCLAIMER,
        }

    @router.get("/api/diff/summary", dependencies=[rate("heavy", _flag("refresh"))])
    def diff_summary(
        stock: Annotated[str, Query(pattern=STOCK)],
        kind: Annotated[str, Query(pattern="^(사업보고서|반기보고서|분기보고서)$")] = "사업보고서",
        refresh: bool = False,
    ):
        """최근 두 보고서의 변경점 요약 (새 위험, 빠진 내용, 주요 변경, 숫자 변화).

        처음 요청할 때 답변 모델로 만들고 저장해 두었다가 다시 쓴다."""
        from dartrag.answer import LLMError
        from dartrag.changes.summary import CATEGORIES, latest_digest

        with services.repo() as repo:
            code, *_ = company_or_404(repo, stock)
            llm = services.llm() if services.llm else None
            try:
                digest = latest_digest(repo, code, llm, kind, refresh=refresh)
            except LLMError as e:
                raise HTTPException(503, str(e)) from None
            except ValueError as e:
                raise HTTPException(404, str(e)) from None
        if digest is None:
            raise HTTPException(404, f"비교할 {kind}가 두 건 이상 없습니다")
        titles = {v: k for k, v in CATEGORIES.items()}
        return digest.to_dict() | {
            "titles": titles,
            "old_url": DART_URL.format(digest.old["rcept_no"]),
            "new_url": DART_URL.format(digest.new["rcept_no"]),
            "disclaimer": DISCLAIMER,
        }

    @router.get("/api/company/{stock}/report.pdf", dependencies=[rate("heavy", _flag("llm"))])
    def report_pdf(
        stock: Annotated[str, Path(pattern=STOCK)],
        llm: bool = False,
    ):
        """기업 리포트 PDF. llm=true 면 사업 개요·위험 요인 요약을 넣는다 (오래 걸림)."""
        from dartrag.report import build_report, render_pdf

        with services.repo() as repo:
            try:
                report = build_report(
                    repo, stock, answerer=services.answerer(repo) if llm else None
                )
            except LookupError as e:
                raise HTTPException(404, str(e)) from None
        pdf = render_pdf(report, services.report_font)
        filename = f"{report.corp_name}_{report.generated_at:%Y%m%d}.pdf"
        return Response(
            pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="report_{stock}.pdf"; '
                    f"filename*=UTF-8''{quote(filename)}"
                ),
                "Cache-Control": "no-store",
            },
        )

    return router
