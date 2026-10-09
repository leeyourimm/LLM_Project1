"""한 문항 채점.

LLM 채점관 없이 규칙으로 채점한다. 로컬 모델로 채점하면 점수가 흔들려서
회귀 테스트로 쓰기 어렵기 때문이다. 서술형 품질은 키워드·근거 위치로만 본다.
"""

from dataclasses import dataclass, field

from dartrag.answer import Answer
from dartrag.answer.numbers import extract, matches
from dartrag.eval.cases import EvalCase


@dataclass
class Grade:
    case_id: str
    category: str
    passed: bool
    retrieval_hit: bool | None = None  # 근거 위치를 아는 문항만
    number_recall: float | None = None
    keyword_recall: float | None = None
    abstained: bool = False  # "찾지 못했습니다" 라고 답함
    cited: bool = False
    invalid_citation: bool = False
    unverified_numbers: int = 0
    latency_s: float = 0.0
    reasons: list[str] = field(default_factory=list)
    answer: str = ""


def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def retrieval_hit(case: EvalCase, answer: Answer) -> bool | None:
    if not case.expected_sources:
        return None
    for exp in case.expected_sources:
        for h in answer.hits:
            c = h.chunk
            if exp.finance:
                if c.get("kind") == "finance":
                    return True
                continue
            if exp.rcept_no and c.get("rcept_no") != exp.rcept_no:
                continue
            if exp.section and _norm(exp.section) not in _norm(" ".join(c.get("section_path", []))):
                continue
            return True
    return False


def number_recall(expected: list[str], text: str) -> float | None:
    if not expected:
        return None
    got = extract(text)
    hits = 0
    for e in expected:
        qs = extract(e)
        if qs and any(matches(q, g) or matches(g, q) for q in qs for g in got):
            hits += 1
    return hits / len(expected)


def keyword_recall(expected: list[str], text: str) -> float | None:
    if not expected:
        return None
    t = _norm(text)
    return sum(_norm(k) in t for k in expected) / len(expected)


def grade(case: EvalCase, answer: Answer, latency_s: float = 0.0) -> Grade:
    g = Grade(
        case.id,
        case.category,
        passed=False,
        retrieval_hit=retrieval_hit(case, answer),
        number_recall=number_recall(case.expected_numbers, answer.text),
        keyword_recall=keyword_recall(case.expected_keywords, answer.text),
        abstained=not answer.found,
        cited=bool(answer.citations),
        invalid_citation=any("존재하지 않는 출처" in w for w in answer.warnings),
        unverified_numbers=len(answer.unverified),
        latency_s=latency_s,
        answer=answer.text,
    )
    if case.category == "unanswerable":
        if not g.abstained:
            g.reasons.append("답이 없는 질문인데 답함")
    else:
        if g.abstained:
            g.reasons.append("답할 수 있는 질문인데 못 찾았다고 함")
        if not g.cited:
            g.reasons.append("출처 표시 없음")
        if g.invalid_citation:
            g.reasons.append("없는 출처 번호 인용")
        if g.number_recall is not None and g.number_recall < 1:
            g.reasons.append(f"정답 숫자 일부 누락·오류 ({g.number_recall:.0%})")
        if g.keyword_recall is not None and g.keyword_recall < 0.5:
            g.reasons.append(f"핵심 키워드 부족 ({g.keyword_recall:.0%})")
        if g.retrieval_hit is False:
            g.reasons.append("정답 근거를 검색하지 못함")
    g.passed = not g.reasons
    return g
