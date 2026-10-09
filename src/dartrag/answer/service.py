"""질문 → 하이브리드 검색 → LLM 답변 → 인용 검증."""

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from dartrag.answer.gateway import last_call
from dartrag.answer.guard import check_question
from dartrag.answer.llm import _THINK_RE, LLM
from dartrag.answer.numbers import checked_count, split_sentences, unverified_numbers
from dartrag.answer.prompt import NOT_FOUND, build_messages, source_text
from dartrag.obs import metrics
from dartrag.obs.tracing import NOOP, Tracer
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
    # 신뢰도 표시용: 출처 목록에 없는 인용 번호, 원문과 대조한 숫자 개수
    invalid_citations: list[int] = field(default_factory=list)
    numbers_checked: int = 0
    refused: str | None = None  # advice / injection: 정책상 답하지 않은 질문
    cached: bool = False
    model: str | None = None  # 실제로 답한 모델 (대체 모델이 답했으면 그 이름)


class FinanceLookup(Protocol):
    def lookup(self, question: str, flt: SearchFilter | None = None) -> SearchHit | None: ...


class _Run:
    """질문 하나의 단계별 시간과 추적 기록."""

    def __init__(
        self, tracer: Tracer, question: str, flt: SearchFilter, user_id, session_id, model: str
    ):
        self.started = time.monotonic()
        self.timings: dict[str, float] = {}
        self.model = model  # 답한 모델. 게이트웨이가 대체 모델로 답하면 바뀐다
        self.fallback = False
        self.trace = tracer.trace(
            "answer",
            input=question,
            user_id=user_id,
            session_id=session_id,
            metadata={
                "corp_codes": flt.corp_codes,
                "year_from": flt.year_from,
                "year_to": flt.year_to,
            },
        )

    def retrieved(self, question: str, hits: list[SearchHit], start: datetime, t0: float):
        self.timings["retrieve"] = time.monotonic() - t0
        self.trace.span(
            "retrieve",
            start,
            datetime.now(UTC),
            input=question,
            output=[
                {
                    "chunk_id": h.chunk_id,
                    "score": round(h.score, 4) if h.score is not None else None,
                    "rerank": h.rerank_score,
                    "corp_name": h.chunk.get("corp_name"),
                    "section": " > ".join(h.chunk.get("section_path", [])),
                }
                for h in hits
            ],
        )

    def finish(self, result: "Answer", usage: dict | None) -> "Answer":
        self.timings["total"] = time.monotonic() - self.started
        if result.model is None:
            result.model = self.model
        metrics.record_answer(result, self.timings, usage, result.model)
        self.trace.end(
            output=result.text,
            metadata={
                "found": result.found,
                "refused": result.refused,
                "cached": result.cached,
                "warnings": result.warnings,
                "unverified_numbers": result.unverified,
                "cited": [c.number for c in result.citations],
            },
        )
        return result

    def failed(self, error: Exception) -> None:
        metrics.ANSWERS.labels("error").inc()
        self.trace.end(output=None, metadata={"error": type(error).__name__})


class Answerer:
    def __init__(
        self,
        retriever: HybridRetriever,
        llm: LLM,
        *,
        finance: FinanceLookup | None = None,
        top_k: int = 8,
        cache=None,  # AnswerCache
        tracer: Tracer = NOOP,
    ):
        self.retriever = retriever
        self.llm = llm
        self.finance = finance
        self.top_k = top_k
        self.cache = cache
        self.tracer = tracer

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

    def _retrieve(self, run: _Run, question: str, flt: SearchFilter) -> list[SearchHit]:
        start, t0 = datetime.now(UTC), time.monotonic()
        hits = self.retrieve(question, flt)
        run.retrieved(question, hits, start, t0)
        return hits

    def _usage(self) -> dict | None:
        fn = getattr(self.llm, "usage", None)
        return fn() if callable(fn) else None

    def _generation(self, run: _Run, messages, text, start, t0, first_token=None, error=None):
        run.timings["generate"] = time.monotonic() - t0
        usage = self._usage()
        # 게이트웨이를 거쳤으면 실제로 답한 모델과 재시도·대체 기록을 함께 남긴다
        call = last_call(self.llm)
        if call:
            run.model, run.fallback = call.model, call.fallback
        run.trace.generation(
            "generate",
            start,
            datetime.now(UTC),
            model=run.model,
            input=[{"role": m.role, "content": m.content} for m in messages],
            output=text,
            usage=usage,
            first_token=first_token,
            metadata=call.metadata() if call else None,
            level="ERROR" if error else None,
        )
        return usage

    def _remember(self, run: _Run, question: str, flt: SearchFilter, result: Answer) -> None:
        # 대체 모델의 답은 캐시에 넣지 않는다. 기본 모델 이름으로 저장되어 기본 모델이
        # 돌아온 뒤에도 한동안 대체 모델의 답이 나가게 된다
        if self.cache and not run.fallback:
            self.cache.put(question, flt, result)

    def answer(
        self,
        question: str,
        flt: SearchFilter | None = None,
        *,
        user_id=None,
        session_id=None,
    ) -> Answer:
        flt = flt or SearchFilter()
        run = _Run(self.tracer, question, flt, user_id, session_id, self.llm.name)
        if early := self._precheck(question, flt):
            return run.finish(early, None)
        usage = None
        try:
            hits = self._retrieve(run, question, flt)
            if not hits:
                result = Answer(question, NOT_FOUND, found=False)
            else:
                messages = build_messages(question, hits)
                start, t0 = datetime.now(UTC), time.monotonic()
                try:
                    text = self.llm.chat(messages)
                except Exception as e:
                    self._generation(run, messages, None, start, t0, error=e)
                    raise
                usage = self._generation(run, messages, text, start, t0)
                result = check_citations(Answer(question, text, hits=hits))
        except Exception as e:
            run.failed(e)
            raise
        self._remember(run, question, flt, result)
        return run.finish(result, usage)

    def stream(
        self,
        question: str,
        flt: SearchFilter | None = None,
        *,
        user_id=None,
        session_id=None,
    ) -> Iterator[tuple]:
        """("sources", hits) → ("token", 조각)… → ("done", Answer) 순서로 낸다."""
        flt = flt or SearchFilter()
        run = _Run(self.tracer, question, flt, user_id, session_id, self.llm.name)
        if early := self._precheck(question, flt):
            if early.hits:
                yield ("sources", early.hits)
            yield ("token", early.text)
            yield ("done", run.finish(early, None))
            return
        try:
            hits = self._retrieve(run, question, flt)
        except Exception as e:
            run.failed(e)
            raise
        if not hits:
            result = Answer(question, NOT_FOUND, found=False)
            self._remember(run, question, flt, result)
            yield ("done", run.finish(result, None))
            return
        yield ("sources", hits)
        messages = build_messages(question, hits)
        parts: list[str] = []
        start, t0 = datetime.now(UTC), time.monotonic()
        first_token = None
        try:
            if hasattr(self.llm, "stream"):
                for piece in self.llm.stream(messages):
                    if first_token is None:
                        first_token = datetime.now(UTC)
                        run.timings["first_token"] = time.monotonic() - t0
                    parts.append(piece)
                    yield ("token", piece)
            else:
                parts.append(self.llm.chat(messages))
                yield ("token", parts[0])
        except Exception as e:
            self._generation(run, messages, "".join(parts), start, t0, first_token, error=e)
            run.failed(e)
            raise
        text = _THINK_RE.sub("", "".join(parts)).strip()
        usage = self._generation(run, messages, text, start, t0, first_token)
        result = check_citations(Answer(question, text, hits=hits))
        self._remember(run, question, flt, result)
        yield ("done", run.finish(result, usage))


def check_citations(answer: Answer) -> Answer:
    if NOT_FOUND in answer.text:
        answer.found = False
        return answer
    cited = sorted({int(n) for n in _CITE_RE.findall(answer.text)})
    valid = [n for n in cited if 1 <= n <= len(answer.hits)]
    invalid = [n for n in cited if n not in valid]
    answer.citations = [Citation(n, answer.hits[n - 1]) for n in valid]
    answer.invalid_citations = invalid
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
        answer.numbers_checked += checked_count(sentence)
    if answer.unverified:
        answer.warnings.append(
            "인용한 원문에서 확인되지 않은 숫자(오기이거나 계산값): " + ", ".join(answer.unverified)
        )
