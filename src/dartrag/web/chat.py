"""질문·답변 API: 이어지는 질문, 대화 기록, 스트리밍, 답변 평가."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dartrag.answer.conversation import Resolved, TurnContext, resolve
from dartrag.answer.evidence import passage, quoted
from dartrag.answer.trust import assess
from dartrag.obs import metrics
from dartrag.search import SearchFilter

log = logging.getLogger(__name__)

DISCLAIMER = "공시 정보 요약이며 투자 권유가 아닙니다. 중요한 판단은 원문을 확인하세요."
STREAM_FAILED = "답변을 만들다 서버에서 오류가 났습니다. 잠시 뒤 다시 물어봐 주세요."
# 이 시간 동안 보낼 이벤트가 없으면 연결 유지용 주석 줄을 보낸다
KEEPALIVE_SECONDS = 10.0
FEEDBACK_REASONS = ("wrong_number", "wrong_source", "not_found", "unhelpful", "other")


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    stocks: list[str] = Field(default_factory=list, max_length=5)
    year_from: int | None = None
    year_to: int | None = None
    conversation_id: int | None = None


class FeedbackRequest(BaseModel):
    rating: Literal[-1, 1]
    reason: Literal[FEEDBACK_REASONS] | None = None  # type: ignore[valid-type]
    comment: str | None = Field(None, max_length=1000)


def source_dict(number: int | None, hit) -> dict:
    c = hit.chunk
    rcept_dt = c.get("rcept_dt")
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
        "rcept_no": c.get("rcept_no"),
        # 대화 기록(JSONB)에 그대로 저장되므로 날짜는 문자열로
        "rcept_dt": rcept_dt.isoformat() if isinstance(rcept_dt, date) else rcept_dt,
        # 출처 패널: 모델이 읽은 앞뒤 문단(context)과 그 안의 인용 문단 위치(highlight)
        **passage(hit),
    }


def _sources(hits, cited: set[int] | None = None, quotes: dict | None = None) -> list[dict]:
    out = []
    for i, h in enumerate(hits, start=1):
        d = source_dict(i, h)
        if cited is not None:
            d["cited"] = i in cited
            # 답변에 옮긴 숫자가 원문(context 또는 body)에서 있는 위치
            d["quoted"] = (quotes or {}).get(i, [])
        out.append(d)
    return out


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def keepalive(events: Iterator[str], every: float = KEEPALIVE_SECONDS) -> AsyncIterator[str]:
    """이벤트 사이가 every 초보다 길어지면 SSE 주석 줄(": keep-alive")을 보낸다.

    첫 질문에 임베딩 모델을 불러오거나 LLM 이 첫 글자를 내기까지 수십 초가 걸릴 수 있다.
    그동안 아무것도 보내지 않으면 중간 프록시(Next 개발 서버는 30초, Cloudflare 는 100초)가
    연결을 끊는다. 브라우저의 SSE 읽기는 주석 줄을 무시한다.
    """
    from anyio import to_thread

    end = object()
    pending: asyncio.Task | None = None
    while True:
        if pending is None:
            pending = asyncio.ensure_future(to_thread.run_sync(next, events, end))
        done, _ = await asyncio.wait({pending}, timeout=every)
        if not done:
            yield ": keep-alive\n\n"
            continue
        item = pending.result()
        pending = None
        if item is end:
            return
        yield item


def _no_limit(scope: str, when=None):
    from fastapi import Depends

    return Depends(lambda: None)


def build_router(  # noqa: N803
    services, CurrentUser, corp_codes: Callable, rate: Callable = _no_limit
) -> APIRouter:
    router = APIRouter()

    def uid(user) -> int | None:
        return user.id if user else None

    def prepare(repo, req: AskRequest, user) -> tuple[int, Resolved, SearchFilter, int]:
        previous = None
        conv_id = req.conversation_id
        if conv_id is not None:
            if not repo.owns_conversation(conv_id, uid(user)):
                raise HTTPException(404, "대화를 찾을 수 없습니다")
            previous = TurnContext.from_dict(repo.last_context(conv_id))
        resolved = resolve(req.question, previous, repo.listed_companies())
        # 회사를 알면 그 회사 문서만 찾아서 다른 회사 내용이 섞이지 않게 한다
        codes = corp_codes(repo, req.stocks) or resolved.context.corp_codes
        flt = SearchFilter(corp_codes=codes, year_from=req.year_from, year_to=req.year_to)
        if conv_id is None:
            conv_id = repo.create_conversation(uid(user), req.question)
        message_id = repo.add_message(
            conv_id,
            "user",
            req.question,
            {
                "resolved_question": resolved.question,
                "inherited": resolved.inherited,
                "context": resolved.context.to_dict(),
            },
        )
        return conv_id, resolved, flt, message_id

    def finish(repo, conv_id: int, resolved: Resolved, result, model: str, started: float):
        cited = {c.number for c in result.citations}
        payload = {
            "found": result.found,
            "refused": result.refused,
            "cached": result.cached,
            "warnings": result.warnings,
            "unverified_numbers": result.unverified,
            # 답변 신뢰도 표시: 근거 수, 최신 공시 여부, 검증 결과 (답변 시점 기준으로 저장)
            "trust": assess(result, repo.filing_freshness, date.today()),
            "sources": _sources(result.hits, cited, quoted(result)),
            "model": model,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        message_id = repo.add_message(conv_id, "assistant", result.text, payload)
        return {
            "conversation_id": conv_id,
            "message_id": message_id,
            "question": resolved.question,
            "inherited": resolved.inherited,
            "answer": result.text,
            **payload,
            "disclaimer": DISCLAIMER,
        }

    @router.post("/api/ask", dependencies=[rate("ask")])
    def ask(req: AskRequest, user: CurrentUser = None):
        from dartrag.answer import LLMError

        started = time.monotonic()
        with services.repo() as repo:
            conv_id, resolved, flt, _ = prepare(repo, req, user)
            answerer = services.answerer(repo)
            try:
                result = answerer.answer(
                    resolved.question, flt, user_id=uid(user), session_id=conv_id
                )
            except LLMError as e:
                raise HTTPException(503, str(e)) from None
            model = result.model or answerer.llm.name
            return finish(repo, conv_id, resolved, result, model, started)

    @router.post("/api/ask/stream", dependencies=[rate("ask")])
    def ask_stream(req: AskRequest, user: CurrentUser = None):
        """Server-Sent Events: meta → token… → done (오류는 error)."""
        from dartrag.answer import LLMError

        started = time.monotonic()
        # 요청이 잘못됐으면(없는 대화 등) 스트림을 열기 전에 오류로 돌려준다
        with services.repo() as repo:
            conv_id, resolved, flt, _ = prepare(repo, req, user)

        def events() -> Iterator[str]:
            # 스트림을 연 뒤에 난 오류는 상태 코드로 알릴 수 없으므로 error 이벤트로 보낸다.
            # 그냥 던지면 연결이 끊겨 화면에는 "연결이 끊겼습니다"만 보인다
            try:
                with services.repo() as repo:
                    answerer = services.answerer(repo)
                    yield _sse(
                        "meta",
                        {
                            "conversation_id": conv_id,
                            "question": resolved.question,
                            "inherited": resolved.inherited,
                        },
                    )
                    stream = answerer.stream(
                        resolved.question, flt, user_id=uid(user), session_id=conv_id
                    )
                    for kind, value in stream:
                        if kind == "sources":
                            yield _sse("sources", {"sources": _sources(value)})
                        elif kind == "token":
                            yield _sse("token", {"text": value})
                        else:
                            model = value.model or answerer.llm.name
                            done = finish(repo, conv_id, resolved, value, model, started)
                            yield _sse("done", done)
            except LLMError as e:
                yield _sse("error", {"detail": str(e)})
            except Exception:
                log.exception("스트리밍 답변 실패 (대화 %s)", conv_id)
                yield _sse("error", {"detail": STREAM_FAILED})

        return StreamingResponse(
            keepalive(events()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/conversations")
    def conversations(user: CurrentUser = None):
        with services.repo() as repo:
            return repo.conversations(uid(user))

    @router.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: Annotated[int, Path()], user: CurrentUser = None):
        with services.repo() as repo:
            if not repo.owns_conversation(conversation_id, uid(user)):
                raise HTTPException(404, "대화를 찾을 수 없습니다")
            return {"id": conversation_id, "messages": repo.messages(conversation_id)}

    @router.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: Annotated[int, Path()], user: CurrentUser = None):
        with services.repo() as repo:
            if not repo.delete_conversation(conversation_id, uid(user)):
                raise HTTPException(404, "대화를 찾을 수 없습니다")
        return {"ok": True}

    @router.post("/api/messages/{message_id}/feedback")
    def feedback(
        message_id: Annotated[int, Path()], req: FeedbackRequest, user: CurrentUser = None
    ):
        with services.repo() as repo:
            ok = repo.set_feedback(message_id, uid(user), req.rating, req.reason, req.comment)
        if not ok:
            raise HTTPException(404, "평가할 답변을 찾을 수 없습니다")
        metrics.FEEDBACK.labels("up" if req.rating == 1 else "down").inc()
        return {"ok": True}

    return router
