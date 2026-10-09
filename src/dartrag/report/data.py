"""리포트에 들어갈 내용을 모은다. 그리는 일(pdf.py)과 나눠서 따로 시험할 수 있게 한다."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from dartrag.finance.series import QuarterPoint, YearPoint, company_series, quarterly_series
from dartrag.search import SearchFilter

log = logging.getLogger(__name__)

DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
DISCLAIMER = (
    "이 리포트는 금융감독원 전자공시(DART)에 제출된 공시를 자동으로 요약한 자료이며 "
    "투자 권유가 아닙니다. 숫자와 내용은 반드시 원문 공시에서 확인하세요."
)
# 근거 문서를 찾아 답하는 질문. {name} 은 회사 이름
REPORT_QUESTIONS = (
    ("사업 개요", "{name}의 주요 사업 부문과 매출 구성을 최근 사업보고서 기준으로 요약해줘"),
    ("주요 위험 요인", "{name}의 사업보고서에 적힌 주요 위험 요인을 요약해줘"),
)


@dataclass
class ReportSource:
    number: int
    label: str  # 회사 · 보고서 · 섹션
    url: str | None


@dataclass
class ReportSection:
    title: str
    text: str
    sources: list[ReportSource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DiffItem:
    section: str
    status: str  # added / removed / changed
    added: int
    removed: int
    modified: int
    sample: str | None  # 새로 들어간 문장 하나 (있으면)


@dataclass
class DiffSummary:
    old: dict
    new: dict
    items: list[DiffItem]


@dataclass
class CompanyReport:
    corp_code: str
    corp_name: str
    stock_code: str | None
    generated_at: datetime
    series: list[YearPoint]
    quarters: list[QuarterPoint]
    disclosures: list[dict]
    diff: DiffSummary | None = None
    sections: list[ReportSection] = field(default_factory=list)
    llm_model: str | None = None
    notes: list[str] = field(default_factory=list)  # 일부를 만들지 못한 이유


def _source_label(hit) -> str:
    c = hit.chunk
    parts = [c.get("corp_name"), c.get("report_nm"), " > ".join(c.get("section_path", []))]
    return " · ".join(p for p in parts if p)


def answer_section(title: str, answer) -> ReportSection:
    """답변을 리포트 섹션으로. 실제로 인용한 근거만 출처로 남긴다."""
    cited = sorted({c.number for c in answer.citations})
    sources = [
        ReportSource(n, _source_label(answer.hits[n - 1]), answer.hits[n - 1].chunk.get("url"))
        for n in cited
        if 1 <= n <= len(answer.hits)
    ]
    return ReportSection(title, answer.text, sources, list(answer.warnings))


def diff_summary(repo, corp_code: str, limit: int = 8) -> DiffSummary | None:
    from dartrag.changes.compare import compare_filings, latest_pair

    pair = latest_pair(repo, corp_code)
    if pair is None:
        return None
    result = compare_filings(repo, *pair)
    items = [
        DiffItem(
            d.key,
            d.status,
            len(d.added),
            len(d.removed),
            len(d.modified),
            next((s for s in d.added if len(s) >= 20), None),
        )
        for d in result.diffs
        if d.substantive
    ][:limit]
    return DiffSummary(result.old, result.new, items)


def build_report(
    repo,
    stock_code: str,
    *,
    answerer=None,
    years: int = 5,
    quarters: int = 8,
    days: int = 180,
    today: date | None = None,
) -> CompanyReport:
    found = repo.company_by_stock(stock_code)
    if found is None:
        raise LookupError(f"종목코드 {stock_code} 의 기업이 없습니다")
    corp_code, name, stock = found
    today = today or date.today()
    report = CompanyReport(
        corp_code=corp_code,
        corp_name=name,
        stock_code=stock,
        generated_at=datetime.now(),
        series=company_series(repo, corp_code, years),
        quarters=quarterly_series(repo, corp_code, quarters),
        disclosures=[
            d | {"url": DART_URL.format(d["rcept_no"])}
            for d in repo.recent_disclosures(today - timedelta(days=days), 2, [corp_code])[:15]
        ],
    )
    try:
        report.diff = diff_summary(repo, corp_code)
    except ValueError as e:  # 아직 파싱하지 않은 보고서 등
        report.notes.append(f"변경점: {e}")
    if answerer is not None:
        from dartrag.answer import LLMError

        report.llm_model = answerer.llm.name
        flt = SearchFilter(corp_codes=[corp_code])
        models: list[str] = []
        for title, question in REPORT_QUESTIONS:
            try:
                answer = answerer.answer(question.format(name=name), flt)
            except LLMError as e:
                report.notes.append(f"{title}: 답변 모델을 쓸 수 없어 생략 ({e})")
                continue
            report.sections.append(answer_section(title, answer))
            models.append(answer.model or answerer.llm.name)
        if models:  # 대체 모델이 답한 요약이 있으면 그 이름도 적는다
            report.llm_model = ", ".join(dict.fromkeys(models))
    return report
