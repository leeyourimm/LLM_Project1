from datetime import date, datetime

from dartrag.answer import Answer, LLMError
from dartrag.answer.service import check_citations
from dartrag.finance.series import QuarterPoint, YearPoint
from dartrag.report import build_report, render_pdf
from dartrag.report.data import CompanyReport, answer_section
from dartrag.report.pdf import CID_FONT, chart_unit, eok, register_font
from dartrag.search import SearchHit


def hit(section, url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1"):
    return SearchHit(
        section,
        0.1,
        1,
        1,
        {
            "corp_name": "삼성전자",
            "report_nm": "사업보고서 (2024.12)",
            "section_path": ["II. 사업의 내용", section],
            "body": "본문",
            "url": url,
        },
    )


def test_answer_section_keeps_only_cited_sources():
    a = check_citations(
        Answer("q", "DX와 DS 부문이 있습니다 [2].", hits=[hit("개요"), hit("주요 제품")])
    )
    s = answer_section("사업 개요", a)
    assert [x.number for x in s.sources] == [2]
    assert s.sources[0].label == "삼성전자 · 사업보고서 (2024.12) · II. 사업의 내용 > 주요 제품"


class Repo:
    def company_by_stock(self, stock):
        return ("00126380", "삼성전자", "005930") if stock == "005930" else None

    def financial_rows(self, *a):
        return []

    def quarter_rows(self, *a):
        return []

    def recent_disclosures(self, since, min_importance, codes):
        self.since = since
        return [{"rcept_no": "20261001000001", "report_nm": "x", "rcept_dt": date(2026, 10, 1)}]

    def latest_filing_per_period(self, code, kind):
        return ["old"]


class LLM:
    name = "fake"


class Answerer:
    llm = LLM()

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.questions = []

    def answer(self, question, flt):
        self.questions.append((question, flt.corp_codes))
        if self.fail_on and self.fail_on in question:
            raise LLMError("Ollama 에 연결할 수 없습니다.")
        return check_citations(Answer(question, "요약입니다 [1].", hits=[hit("개요")]))


def test_build_report_collects_parts():
    repo = Repo()
    r = build_report(repo, "005930", answerer=Answerer(fail_on="위험"), today=date(2026, 10, 12))
    assert r.corp_name == "삼성전자" and r.llm_model == "fake"
    assert repo.since == date(2026, 4, 15)
    assert r.disclosures[0]["url"].endswith("rcpNo=20261001000001")
    assert r.diff is None
    assert [s.title for s in r.sections] == ["사업 개요"]
    assert any("위험 요인" in n and "생략" in n for n in r.notes)


def test_build_report_unknown_stock():
    import pytest

    with pytest.raises(LookupError):
        build_report(Repo(), "000000")


def test_render_pdf_with_and_without_data(tmp_path):
    empty = CompanyReport("c", "빈회사", None, datetime(2026, 10, 12), [], [], [])
    assert render_pdf(empty).startswith(b"%PDF")
    p = YearPoint(2024, {"revenue": 300 * 10**12, "operating_income": -5 * 10**12}, fs_div="연결")
    q = [
        QuarterPoint(2024, 3, {"revenue": 80 * 10**12}),
        QuarterPoint(2024, 4, {"revenue": 75 * 10**12}, ["revenue"]),
    ]
    full = CompanyReport("c", "삼성전자", "005930", datetime(2026, 10, 12), [p], q, [])
    pdf = render_pdf(full)
    assert pdf.startswith(b"%PDF") and len(pdf) > len(render_pdf(empty))


def test_font_fallback_and_formatting(tmp_path, monkeypatch):
    from dartrag.report import pdf

    monkeypatch.setattr(pdf, "FONT_CANDIDATES", ())
    assert register_font(str(tmp_path / "없음.ttf")) == CID_FONT
    assert eok(300_870_903_000_000) == "3,008,709" and eok(None) == "-"
    assert chart_unit([5 * 10**11]) == (10**8, "억원")
    assert chart_unit([-2 * 10**12]) == (10**12, "조원")
