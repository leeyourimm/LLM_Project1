import json
from pathlib import Path

import pytest

from dartrag.answer import NOT_FOUND, Answer
from dartrag.answer.service import check_citations
from dartrag.eval import EvalCase, ExpectedSource, grade, load_cases, run_eval, write_report
from dartrag.eval.generate import generate_cases
from dartrag.eval.grading import number_recall
from dartrag.finance.tool import Value
from dartrag.search import SearchHit

ROOT = Path(__file__).resolve().parents[1]


def finance_hit(body="| 삼성전자 | 2024 | 연결 | 매출액 | 300조 8,709억원 |"):
    return SearchHit(
        "finance",
        1.0,
        None,
        None,
        {"kind": "finance", "section_path": ["재무 데이터"], "body": body, "unit": None},
    )


def text_hit(section):
    return SearchHit(
        "c1",
        0.1,
        1,
        1,
        {"rcept_no": "20250311000001", "section_path": section, "body": "본문", "unit": None},
    )


def answer(text, hits):
    return check_citations(Answer("q", text, hits=hits))


NUMERIC = EvalCase(
    "n1",
    "삼성전자 2024년 매출액은?",
    "numeric",
    expected_numbers=["300조 8,709억원"],
    expected_sources=[ExpectedSource(finance=True)],
)


def test_numeric_pass_allows_rounding_in_answer():
    g = grade(NUMERIC, answer("2024년 매출액은 약 300.9조원입니다 [1].", [finance_hit()]), 1.5)
    assert g.passed, g.reasons
    assert g.number_recall == 1 and g.retrieval_hit and g.latency_s == 1.5


def test_numeric_fail_reasons():
    g = grade(NUMERIC, answer("매출액은 30조원입니다.", [text_hit(["I. 회사의 개요"])]))
    assert not g.passed
    assert "출처 표시 없음" in g.reasons
    assert any("정답 숫자" in r for r in g.reasons)
    assert "정답 근거를 검색하지 못함" in g.reasons


def test_unanswerable_and_abstain():
    case = EvalCase("u1", "주가 오를까?", "unanswerable")
    assert grade(case, answer(NOT_FOUND, [text_hit(["x"])])).passed
    assert not grade(case, answer("오릅니다 [1].", [text_hit(["x"])])).passed
    abstain = grade(NUMERIC, answer(NOT_FOUND, [finance_hit()]))
    assert "답할 수 있는 질문인데 못 찾았다고 함" in abstain.reasons


def test_text_case_section_and_keywords():
    case = EvalCase(
        "t1",
        "주요 사업 부문은?",
        "text",
        expected_keywords=["DX", "DS", "SDC"],
        expected_sources=[ExpectedSource(section="사업의 개요")],
    )
    hits = [text_hit(["II. 사업의 내용", "1. 사업의 개요"])]
    g = grade(case, answer("DX 부문과 DS 부문으로 구성됩니다 [1].", hits))
    assert g.passed and g.keyword_recall == pytest.approx(2 / 3)
    g = grade(case, answer("반도체를 만듭니다 [1].", hits))
    assert "핵심 키워드 부족 (0%)" in g.reasons


def test_number_recall_partial():
    assert number_recall(["16.2%", "10.9%"], "증가율 16.2%, 이익률 11.9%") == 0.5
    assert number_recall([], "x") is None


def test_load_cases_validates(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text(
        '{"id": "a", "question": "q", "category": "text"}\n// 주석\n\n'
        '{"id": "b", "question": "q", "category": "numeric", '
        '"expected_sources": [{"section": "배당"}]}\n',
        encoding="utf-8",
    )
    cases = load_cases(p)
    assert [c.id for c in cases] == ["a", "b"]
    assert cases[1].expected_sources[0].section == "배당"
    with pytest.raises(ValueError, match="중복 id"):
        load_cases(p, p)
    bad = tmp_path / "b.jsonl"
    bad.write_text('{"id": "x", "question": "q", "category": "essay"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="category"):
        load_cases(bad)


def test_manual_eval_set_is_valid():
    cases = load_cases(ROOT / "eval" / "manual.jsonl")
    assert len(cases) >= 30
    assert {c.category for c in cases} == {"text", "unanswerable"}


def v(amount):
    return Value(amount, "CFS", "x", "20250311000001")


VALUES = {
    "revenue": {
        ("00126380", 2023): v(258_935_494_000_000),
        ("00126380", 2024): v(300_870_903_000_000),
    },
    "operating_income": {
        ("00126380", 2023): v(6_566_976_000_000),
        ("00126380", 2024): v(32_725_961_000_000),
    },
    "net_income": {},
    "total_assets": {},
}


def test_generate_cases_from_financial_values():
    companies = [("00126380", "삼성전자"), ("00164779", "에스케이하이닉스")]
    cases = generate_cases(companies, VALUES, per_company=100, seed=1)
    by_id = {c.id: c for c in cases}
    assert by_id["gen-00126380-2024-revenue"].expected_numbers == ["300조 8,709억원"]
    assert by_id["gen-00126380-2024-revenue-growth"].expected_numbers == ["16.2%"]
    assert by_id["gen-00126380-2024-operating_margin"].expected_numbers == ["10.9%"]
    assert "gen-00126380-2023-revenue-growth" not in by_id  # 전년 데이터 없음
    assert all(c.corp_codes == ["00126380"] for c in cases)  # 데이터 없는 회사는 제외
    assert sum(c.category == "unanswerable" for c in cases) == 1
    assert cases == generate_cases(companies, VALUES, per_company=100, seed=1)  # 재현 가능
    assert len(generate_cases(companies, VALUES, per_company=2, seed=1)) == 3


def test_run_eval_and_report(tmp_path):
    cases = [
        NUMERIC,
        EvalCase("s1", "배당은?", "text", stocks=["005930"]),
        EvalCase("e1", "오류", "text"),
    ]
    seen = []

    def answer_fn(question, flt):
        seen.append(flt.corp_codes)
        if question == "오류":
            raise RuntimeError("ollama down")
        return answer("매출액은 300조 8,709억원입니다 [1].", [finance_hit()])

    grades = run_eval(cases, answer_fn, lambda stocks: ["00126380"] if stocks else [])
    assert seen == [[], ["00126380"], []]
    assert [g.passed for g in grades] == [True, True, False]
    assert "실행 오류: ollama down" in grades[2].reasons

    report = write_report(tmp_path, grades, {"llm": "fake"})
    text = report.read_text(encoding="utf-8")
    assert "| 전체 | 3 | 66.7% |" in text and "`e1`" in text
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["by_category"]["numeric"]["number_recall"] == 1.0
    assert len((tmp_path / "grades.jsonl").read_text(encoding="utf-8").splitlines()) == 3
