"""LLM 게이트웨이: 시간 제한, 일시적 오류 재시도, 대체 모델 (docs/design.md 12절).

OllamaLLM 같은 모델을 감싸고 같은 LLM/StreamingLLM 프로토콜(chat, stream)을 따르므로
답변기·변경점 요약은 그대로 쓴다.

- 시간 제한: 한 번 보낸 요청이 timeout 초를 넘으면 끊는다. 대체 모델이 있으면 기본 모델이
  first_token_timeout 초 안에 첫 글자를 내지 못할 때 더 기다리지 않고 대체 모델로 넘긴다.
  대체 모델이 없을 때는 첫 글자 제한을 쓰지 않는다 (일찍 끊어 봐야 오류만 빨리 날 뿐이다).
- 재시도: 연결 거부, 연결·응답 시간 초과, 5xx, 응답 중 끊김처럼 다시 하면 될 수도 있는 오류만
  retries 번까지, 기다리는 시간을 두 배씩 늘려 가며 다시 보낸다. 모델이 없음(404)처럼 다시 해도
  같은 오류와, 게이트웨이 자신의 시간 제한(느린 모델에 같은 요청을 또 보내면 더 밀린다)은
  다시 보내지 않고 대체 모델로 넘긴다.
- 대체 모델: 기본 모델이 끝내 실패하거나 첫 글자 전에 시간이 다 되면 대체 모델이 답한다.
  스트리밍은 한 글자라도 내보낸 뒤에는 다시 보내지도, 대체 모델로 넘기지도 않는다 (글이 겹치므로).

chat 도 안에서는 스트리밍으로 받는다. 그래야 첫 글자 시간을 재서 멈춘 모델을 일찍 알아챈다.
어떤 모델이 답했는지는 last_call() 로 알 수 있고 지표(dartrag_llm_requests)에도 남는다.
웹 서버는 여러 스레드가 같은 객체를 쓰므로 usage()·last_call() 은 스레드마다 따로 둔다.
"""

import logging
import queue
import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, field

import httpx

from dartrag.answer.llm import _THINK_RE, LLM, TRANSIENT, LLMError, Message
from dartrag.obs import metrics

log = logging.getLogger(__name__)


@dataclass
class CallInfo:
    """chat/stream 한 번의 결과: 어떤 모델이 답했는지, 몇 번 보냈는지, 무엇이 실패했는지."""

    requested: str  # 기본 모델
    model: str  # 실제로 답한 모델 (모두 실패했으면 마지막으로 보낸 모델)
    attempts: int = 0  # 모든 모델에 보낸 요청 수
    fallback: bool = False  # 대체 모델이 답했으면 True
    errors: list[str] = field(default_factory=list)  # 실패한 시도마다 "모델: 오류 종류"

    def metadata(self) -> dict:
        return {
            "requested_model": self.requested,
            "fallback": self.fallback,
            "attempts": self.attempts,
            "errors": self.errors,
        }


def classify(e: BaseException) -> str:
    """오류 종류 (LLMError.kind 와 같은 값). 다른 LLM 구현이 그대로 올린 오류도 나눈다."""
    if isinstance(e, LLMError):
        return e.kind
    if isinstance(e, httpx.ConnectError | ConnectionRefusedError):
        return "connect"
    if isinstance(e, httpx.TimeoutException | TimeoutError):
        return "timeout"
    if isinstance(e, httpx.HTTPStatusError):
        return "server" if e.response.status_code >= 500 else "error"
    if isinstance(e, httpx.TransportError | ConnectionError):
        return "disconnect"
    return "error"


def last_call(llm) -> CallInfo | None:
    fn = getattr(llm, "last_call", None)
    return fn() if callable(fn) else None


def answered_by(llm) -> str | None:
    """llm 이 이 스레드에서 마지막으로 답할 때 실제로 쓴 모델. 게이트웨이가 아니면 llm.name."""
    call = last_call(llm)
    return call.model if call else getattr(llm, "name", None)


_PIECE, _DONE, _ERROR = "piece", "done", "error"


class LLMGateway:
    def __init__(
        self,
        primary: LLM,
        fallback: LLM | None = None,
        *,
        timeout: float | None = 300,
        first_token_timeout: float | None = 30,
        retries: int = 2,
        backoff: float = 0.5,
        max_backoff: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self.timeout = timeout or None
        self.first_token_timeout = first_token_timeout or None
        self.retries = max(0, retries)
        self.backoff = backoff
        self.max_backoff = max_backoff
        self._sleep = sleep
        self._jitter = jitter
        self._local = threading.local()

    @property
    def models(self) -> list[LLM]:
        return [m for m in (self.primary, self.fallback) if m is not None]

    def usage(self) -> dict | None:
        """이 스레드에서 마지막으로 부른 chat/stream 의 {"input": n, "output": n}."""
        return getattr(self._local, "usage", None)

    def last_call(self) -> CallInfo | None:
        """이 스레드에서 마지막으로 부른 chat/stream 의 결과 (어떤 모델이 답했는지 등)."""
        return getattr(self._local, "call", None)

    def chat(self, messages: list[Message]) -> str:
        return _THINK_RE.sub("", "".join(self.stream(messages))).strip()

    def stream(self, messages: list[Message]) -> Iterator[str]:
        """답변을 조각씩. 한 글자라도 내보낸 뒤의 오류는 다시 보내지 않고 그대로 올린다."""
        info = CallInfo(self.name, self.name)
        self._finish(info, None)
        failures: list[BaseException] = []
        models = self.models
        for i, llm in enumerate(models):
            # 첫 글자 제한은 넘겨받을 대체 모델이 있을 때만 쓴다
            first_limit = self.first_token_timeout if i + 1 < len(models) else None
            for attempt in range(self.retries + 1):
                info.attempts += 1
                info.model = llm.name
                started = False
                try:
                    with closing(self._attempt(llm, messages, first_limit)) as pieces:
                        for piece in pieces:
                            started = True
                            yield piece
                except Exception as e:
                    kind = classify(e)
                    info.errors.append(f"{llm.name}: {kind}")
                    metrics.LLM_FAILURES.labels(llm.name, kind).inc()
                    if started:
                        # 이미 보낸 글 뒤에 다른 답을 이어 붙일 수 없다
                        metrics.LLM_REQUESTS.labels(self.name, "error").inc()
                        self._finish(info, None)
                        if isinstance(e, LLMError) or kind == "error":
                            raise
                        raise _as_llm_error([e]) from e
                    failures.append(e)
                    if kind in TRANSIENT and attempt < self.retries:
                        delay = self._delay(attempt)
                        log.warning(
                            "%s 일시적 오류(%s), %.1f초 뒤 다시 보냄 (%d/%d)",
                            llm.name,
                            kind,
                            delay,
                            attempt + 1,
                            self.retries,
                        )
                        self._sleep(delay)
                        continue
                    if kind == "error" and not isinstance(e, LLMError):
                        log.warning("%s 호출 중 예상하지 못한 오류", llm.name, exc_info=e)
                    break
                info.fallback = i > 0
                outcome = "fallback" if info.fallback else "ok"
                metrics.LLM_REQUESTS.labels(llm.name, outcome).inc()
                if info.fallback:
                    log.warning("기본 모델 %s 대신 대체 모델 %s 가 답함", self.name, llm.name)
                self._finish(info, self.usage())
                return
        metrics.LLM_REQUESTS.labels(self.name, "error").inc()
        self._finish(info, None)
        unexpected = [e for e in failures if classify(e) == "error" and not isinstance(e, LLMError)]
        if unexpected:
            raise unexpected[0]  # 코드 문제는 LLMError 로 덮지 않는다 (오류 수집에 남도록)
        raise _as_llm_error(failures) from failures[-1]

    def _finish(self, info: CallInfo, usage: dict | None) -> None:
        # 스트리밍 응답은 조각마다 다른 스레드에서 이어질 수 있어, 끝난 스레드에 다시 적어 둔다
        self._local.call = info
        self._local.usage = usage

    def _delay(self, attempt: int) -> float:
        base = min(self.max_backoff, self.backoff * 2**attempt)
        return base * (0.5 + 0.5 * self._jitter())

    def _attempt(self, llm: LLM, messages: list[Message], first_limit: float | None):
        """요청 하나를 다른 스레드에서 보내고, 조각을 기다리며 시간 제한을 잰다.

        시간이 다 되면 그 스레드에 그만하라고 알리고 바로 돌아온다. 그 스레드는 다음 조각을
        받는 즉시 연결을 닫는다 (Ollama 는 연결이 닫히면 생성을 멈춘다)."""
        out: queue.Queue = queue.Queue()
        stop = threading.Event()

        def pump() -> None:
            try:
                if callable(getattr(llm, "stream", None)):
                    pieces = llm.stream(messages)
                    try:
                        for piece in pieces:
                            if stop.is_set():
                                return
                            out.put((_PIECE, piece))
                    finally:
                        close = getattr(pieces, "close", None)
                        if callable(close):
                            close()
                else:
                    out.put((_PIECE, llm.chat(messages)))
                usage = getattr(llm, "usage", None)  # 모델의 토큰 수도 스레드별이라 여기서 읽는다
                out.put((_DONE, usage() if callable(usage) else None))
            except BaseException as e:  # noqa: BLE001 - 받는 쪽에서 나눈다
                out.put((_ERROR, e))

        threading.Thread(target=pump, name=f"llm-{llm.name}", daemon=True).start()
        start = time.monotonic()
        deadline = start + self.timeout if self.timeout else None
        first_deadline = start + first_limit if first_limit else None
        got_first = False
        try:
            while True:
                limits = [t for t in (deadline, None if got_first else first_deadline) if t]
                until = min(limits) if limits else None
                try:
                    kind, value = out.get(
                        timeout=None if until is None else max(0.0, until - time.monotonic())
                    )
                except queue.Empty:
                    if not got_first and until == first_deadline:
                        message = f"모델이 {first_limit:g}초 안에 답을 시작하지 못했습니다"
                    else:
                        message = f"모델의 답변이 {self.timeout:g}초를 넘었습니다"
                    raise LLMError(f"{llm.name} {message}", kind="deadline") from None
                if kind == _PIECE:
                    got_first = True
                    yield value
                elif kind == _DONE:
                    self._local.usage = value
                    return
                else:
                    raise value
        finally:
            stop.set()


def _as_llm_error(failures: list[BaseException]) -> LLMError:
    """실패한 시도들을 화면에 보여 줄 오류 하나로. 같은 문구는 한 번만."""
    messages = list(dict.fromkeys(str(e) or type(e).__name__ for e in failures))
    return LLMError(" / ".join(messages), kind=classify(failures[-1]))
