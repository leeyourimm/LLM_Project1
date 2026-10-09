"""질문 → 하이브리드 검색 → LLM 답변 → 인용 검증."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from dartrag.answer.guard import check_question
from dartrag.answer.llm import _THINK_RE, LLM
from dartrag.answer.numbers import split_sentences, unverified_numbers
from dartrag.answer.prompt import NOT_FOUND, build_messages, source_text
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
    # 인용한 원문에서 확인되지 않은 숫자 (오기이거나 계산값)
    unverified: list[str] = field(default_factory=list)
    # 근거 번호가 없거나 없는 번호를 인용한 경우, 확인되지 않은 숫자가 있는 경우
    warnings: list[str] = field(default_factory=list)
    refused: str | None = None  # advice / injection: 정책상 답하지 않은 질문
    cached: bool = False


class FinanceLookup(Protocol):
    def lookup(self, question: str, flt: SearchFilter | None = None) -> SearchHit | None: ...


class Answerer:
    def __init__(
        self,
        retriever: HybridRetriever,
        llm: LLM,
        *,
        finance: FinanceLookup | None = None,
        top_k: int = 8,
        cache=None,  # AnswerCache
    ):
        self.retriever = retriever
        self.llm = llm
        self.finance = finance
        self.top_k = top_k
        self.cache = cache

    def _precheck(self, question: str, flt: SearchFilter) -> Answer | None:
        """LLM 을 부르기 전에 끝나는 경우: 거절할 질문, 캐시에 있는 답."""
        verdict = check_question(question)
        if verdict:
            return Answer(question, verdict.reply, found=False, refused=verdict.kind)
        return self.cache.get(question, flt) if self.cache else None

    def retrieve(self, question: str, flt: SearchFilter | None = None) -> list[SearchHit]:
        # 재무 수치 질문이면 재무 DB 조회·계산 결과를 첫 번째 출처로 넣는다
        fin = self.finance.lookup(question, flt) if self.finance else None
        hits = self.retriever.search(question, flt, self.top_k - (1 if fin else 0))
        return [fin, *hits] if fin else hits

    def answer(self, question: str, flt: SearchFilter | None = None) -> Answer:
        flt = flt or SearchFilter()
        if early := self._precheck(question, flt):
            return early
        hits = self.retrieve(question, flt)
        if not hits:
            result = Answer(question, NOT_FOUND, found=False)
        else:
            text = self.llm.chat(build_messages(question, hits))
            result = check_citations(Answer(question, text, hits=hits))
        if self.cache:
            self.cache.put(question, flt, result)
        return result

    def stream(self, question: str, flt: SearchFilter | None = None) -> Iterator[tuple]:
        """("sources", hits) → ("token", 조각)… → ("done", Answer) 순서로 낸다."""
        flt = flt or SearchFilter()
        if early := self._precheck(question, flt):
            if early.hits:
                yield ("sources", early.hits)
            yield ("token", early.text)
            yield ("done", early)
            return
        hits = self.retrieve(question, flt)
        if not hits:
            result = Answer(question, NOT_FOUND, found=False)
            if self.cache:
                self.cache.put(question, flt, result)
            yield ("done", result)
            return
        yield ("sources", hits)
        messages = build_messages(question, hits)
        parts: list[str] = []
        if hasattr(self.llm, "stream"):
            for piece in self.llm.stream(messages):
                parts.append(piece)
                yield ("token", piece)
        else:
            parts.append(self.llm.chat(messages))
            yield ("token", parts[0])
        text = _THINK_RE.sub("", "".join(parts)).strip()
        result = check_citations(Answer(question, text, hits=hits))
        if self.cache:
            self.cache.put(question, flt, result)
        yield ("done", result)


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
    check_numbers(answer)
    return answer


def check_numbers(answer: Answer) -> None:
    """문장마다 숫자를 그 문장이 인용한 출처와 대조한다. 인용이 없는 문장은 전체 출처와 대조."""
    for sentence in split_sentences(answer.text):
        numbers = {int(n) for n in _CITE_RE.findall(sentence)}
        hits = [answer.hits[n - 1] for n in sorted(numbers) if 1 <= n <= len(answer.hits)]
        sources = [(source_text(h), h.chunk.get("unit")) for h in hits or answer.hits]
        answer.unverified += unverified_numbers(sentence, sources)
    if answer.unverified:
        answer.warnings.append(
            "인용한 원문에서 확인되지 않은 숫자(오기이거나 계산값): " + ", ".join(answer.unverified)
        )
