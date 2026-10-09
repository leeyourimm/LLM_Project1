"""출처 패널의 근거 위치(evidence)와 답변 신뢰도 사실(trust)."""

from datetime import date

from dartrag.answer import Answer
from dartrag.answer.cache import answer_from_dict, answer_to_dict
from dartrag.answer.evidence import passage, quoted, shown_text
from dartrag.answer.numbers import checked_count, extract, quoted_spans
from dartrag.answer.service import check_citations
from dartrag.answer.trust import assess, cited_filings
from dartrag.search import SearchHit

TODAY = date(2026, 10, 9)


def hit(cid, body, rcept_no="20250311000001", context=None, unit=None, **extra):
    chunk = {
        "corp_name": "삼성전자",
        "report_nm": "사업보고서 (2024.12)",
        "section_path": ["II. 사업의 내용"],
        "body": body,
        "unit": unit,
        "rcept_no": rcept_no,
        **extra,
    }
    if context:
        chunk["context_body"] = context
    return SearchHit(cid, 0.1, 1, None, chunk)


def test_number_positions_point_into_the_original_text():
    text = "2024년 12월 31일 기준(2024.12) 매출은 300조원 [1][2], 이익은 1조 2,345억원"
    qs = extract(text)
    assert [text[q.start : q.end] for q in qs] == ["300조원", "1조 2,345억원"]
    # 위치는 비교에 쓰지 않는다 (같은 값이면 같은 수량)
    assert extract("300조원")[0] == qs[0]
    assert checked_count("2024년 매출 300조원, 3분기 2회 [1]") == 1


def test_quoted_spans_find_the_numbers_the_answer_used():
    table = "| 부문 | 매출액 |\n| --- | --- |\n| DS | 111,066,000 |\n| DX | 174,887,000 |"
    spans = quoted_spans(["DS 매출은 111조원입니다 [1]."], table, "백만원")
    assert [table[a:b] for a, b in spans] == ["111,066,000"]
    # 원문에 없는 숫자, 검사하지 않는 숫자(연도·작은 정수)는 표시하지 않는다
    assert quoted_spans(["2024년 매출은 112조원, 4회 [1]."], table, "백만원") == []
    assert quoted_spans([], table, "백만원") == []


def test_passage_marks_the_cited_chunk_inside_the_wider_context():
    body = "DS 부문 매출은 111조원이다."
    context = f"반도체 사업을 한다.\n{body}\n메모리 수요가 늘었다."
    p = passage(hit("c1", body, context=context))
    start, end = p["highlight"]
    assert p["context"] == context and context[start:end] == body
    # 넓힌 맥락이 없거나 본문과 같으면 본문 그대로
    assert passage(hit("c2", body)) == {"context": None, "highlight": None}
    assert passage(hit("c3", body, context=body)) == {"context": None, "highlight": None}
    assert shown_text(hit("c4", body, context="딴 글")) == (body, None)


def test_quoted_spans_per_citation_use_only_sentences_citing_it():
    body1 = "DS 부문 매출은 111조원이다."
    context1 = f"반도체 사업을 한다.\n{body1}"
    hits = [
        hit("c1", body1, context=context1),
        hit("c2", "배당금 총액은 9조 8,094억원이다.", rcept_no="20250311000002"),
    ]
    answer = check_citations(
        Answer("q", "DS 매출은 111조원입니다 [1]. 배당 총액은 9.8조원입니다 [2].", hits=hits)
    )
    spans = quoted(answer)
    [[a, b]] = spans[1]
    assert context1[a:b] == "111조원"  # 화면에 보이는 원문(context) 기준 위치
    [[a, b]] = spans[2]
    assert hits[1].chunk["body"][a:b] == "9조 8,094억원"


def test_verifier_counts_survive_the_answer_cache():
    answer = check_citations(
        Answer("q", "매출은 111조원 [1], 이익은 33조원 [3].", hits=[hit("c1", "매출 111조원")])
    )
    assert answer.invalid_citations == [3] and answer.numbers_checked == 2
    assert answer.unverified == ["33조원"]
    again = answer_from_dict(answer_to_dict(answer))
    assert again.invalid_citations == [3] and again.numbers_checked == 2
    # 이 항목들이 생기기 전에 저장한 답
    old = answer_to_dict(answer)
    del old["invalid_citations"], old["numbers_checked"]
    assert answer_from_dict(old).numbers_checked == 0


def fresh(table):
    """Repository.filing_freshness 흉내: 접수번호 → 공시와 그 회사의 최신 정기공시."""
    return lambda nos: {n: table[n] for n in nos if n in table}


SAMSUNG_2024 = {
    "rcept_no": "20250311000001",
    "corp_code": "00126380",
    "corp_name": "삼성전자",
    "report_nm": "사업보고서 (2024.12)",
    "rcept_dt": date(2025, 3, 11),
}
SAMSUNG_LATEST = {
    "rcept_no": "20250814000002",
    "report_nm": "반기보고서 (2025.06)",
    "rcept_dt": date(2025, 8, 14),
    "indexed": False,
}


def test_assess_counts_sources_and_filings():
    hits = [
        hit("c1", "매출 111조원"),
        hit("c2", "이익 33조원"),
        hit("c3", "안 쓴 출처", rcept_no="20250814000002"),
    ]
    answer = check_citations(Answer("q", "매출은 111조원 [1], 이익은 33조원 [2].", hits=hits))
    table = {"20250311000001": SAMSUNG_2024 | {"latest": None}}
    t = assess(answer, fresh(table), TODAY)
    assert (t["sources"], t["filings"], t["numbers_checked"]) == (2, 1, 2)
    assert t["unverified_numbers"] == [] and t["invalid_citations"] == [] and not t["uncited"]
    # 비교할 정기공시를 모르면 최신인지 단정하지 않는다
    assert t["companies"][0]["is_latest"] is None
    assert t["newest"] == {
        "rcept_no": "20250311000001",
        "rcept_dt": "2025-03-11",
        "report_nm": "사업보고서 (2024.12)",
        "corp_name": "삼성전자",
        "age_days": (TODAY - date(2025, 3, 11)).days,
    }
    assert t["checked_on"] == "2026-10-09"


def test_assess_tells_when_a_newer_filing_exists():
    answer = check_citations(Answer("q", "매출은 111조원 [1].", hits=[hit("c1", "매출 111조원")]))
    t = assess(answer, fresh({"20250311000001": SAMSUNG_2024 | {"latest": SAMSUNG_LATEST}}), TODAY)
    [corp] = t["companies"]
    assert corp["is_latest"] is False
    assert corp["cited"] == {
        "rcept_no": "20250311000001",
        "report_nm": "사업보고서 (2024.12)",
        "rcept_dt": "2025-03-11",
    }
    assert corp["latest"] == {
        "rcept_no": "20250814000002",
        "report_nm": "반기보고서 (2025.06)",
        "rcept_dt": "2025-08-14",
        "indexed": False,
    }
    # 인용한 것이 최신 공시면
    latest = {k: SAMSUNG_2024[k] for k in ("rcept_no", "report_nm", "rcept_dt")} | {"indexed": True}
    t = assess(answer, fresh({"20250311000001": SAMSUNG_2024 | {"latest": latest}}), TODAY)
    assert t["companies"][0]["is_latest"] is True


def test_assess_finance_source_uses_every_report_in_its_table():
    finance = hit(
        "finance",
        "| 삼성전자 | 2024 | 연결 | 매출액 | 300조 8,709억원 |",
        rcept_no="20240312000009",
        rcept_nos=["20240312000009", "20250311000001"],
        kind="finance",
    )
    text = "2024년 매출은 300조 8,709억원입니다 [1]."
    answer = check_citations(Answer("q", text, hits=[finance]))
    assert cited_filings(answer) == ["20240312000009", "20250311000001"]
    # DB 에 없는 공시도 접수번호 앞 8자리(접수일)로 날짜를 안다
    t = assess(answer, fresh({}), TODAY)
    assert t["filings"] == 2 and t["companies"] == []
    assert t["newest"]["rcept_dt"] == "2025-03-11" and t["newest"]["report_nm"] is None


def test_assess_reports_verifier_problems_and_skips_non_answers():
    answer = check_citations(Answer("q", "매출이 늘었습니다.", hits=[hit("c1", "매출 111조원")]))
    t = assess(answer, fresh({}), TODAY)
    assert t["uncited"] and t["sources"] == 0 and t["newest"] is None
    refused = Answer("q", "추천하지 않습니다", found=False, refused="advice")
    assert assess(refused, fresh({}), TODAY) is None
    assert assess(Answer("q", "x", found=False), fresh({}), TODAY) is None
