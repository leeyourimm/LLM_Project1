"""질문 → 하이브리드 검색 → LLM 답변 → 인용 검증."""

import re
from dataclasses import dataclass, field

from dartrag.answer.llm import LLM
from dartrag.answer.prompt import NOT_FOUND, build_messages
from dartrag.search import HybridRetriever, SearchFilter, SearchHit

_CITE_RE = re.compile(r"\[(\d{1,2})\]")


@dataclass
class Citation:
    number: int
    hit: SearchHit


@dataclass
class Answer:
    question: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    hits: list[SearchHit] = field(default_factory=list)
    found: bool = True
    # 답에 근거 번호가 하나도 없거나, 없는 번호를 인용한 경우
    warnings: list[str] = field(default_factory=list)


class Answerer:
    def __init__(self, retriever: HybridRetriever, llm: LLM, *, top_k: int = 8):
        self.retriever = retriever
        self.llm = llm
        self.top_k = top_k

    def answer(self, question: str, flt: SearchFilter | None = None) -> Answer:
        hits = self.retriever.search(question, flt, self.top_k)
        if not hits:
            return Answer(question, NOT_FOUND, found=False)
        text = self.llm.chat(build_messages(question, hits))
        return check_citations(Answer(question, text, hits=hits))


def check_citations(answer: Answer) -> Answer:
    if NOT_FOUND in answer.text:
        answer.found = False
        return answer
    cited = sorted({int(n) for n in _CITE_RE.findall(answer.text)})
    valid = [n for n in cited if 1 <= n <= len(answer.hits)]
    invalid = [n for n in cited if n not in valid]
    answer.citations = [Citation(n, answer.hits[n - 1]) for n in valid]
    if invalid:
        answer.warnings.append(f"존재하지 않는 출처 번호를 인용함: {invalid}")
    if not valid:
        answer.warnings.append("근거 출처 표시가 없는 답변")
    return answer
