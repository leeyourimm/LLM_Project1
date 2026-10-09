"""답변 생성용 LLM.

기본은 내 컴퓨터에서 돌리는 Ollama 로컬 모델이다. 다른 LLM 으로 바꿀 때는
LLM 프로토콜(chat)만 구현하면 된다. 시간 제한·재시도·대체 모델은 이것을 감싸는
gateway.LLMGateway 가 맡고, 여기서는 오류를 종류(LLMError.kind)별로 나눠 올리기만 한다.
"""

import json
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class Message:
    role: str  # system / user / assistant
    content: str


class LLM(Protocol):
    name: str

    def chat(self, messages: list[Message]) -> str: ...


class StreamingLLM(LLM, Protocol):
    def stream(self, messages: list[Message]) -> Iterator[str]: ...


# 다시 시도하면 될 수도 있는 오류. 모델이 없음(missing)처럼 다시 해도 같은 오류는 넣지 않는다
TRANSIENT = frozenset({"connect", "timeout", "server", "disconnect"})


class LLMError(RuntimeError):
    """LLM 호출 실패.

    kind: connect(연결 거부) / timeout(연결·응답 시간 초과) / server(5xx) /
    disconnect(응답 중 연결 끊김) / missing(모델 없음) / deadline(게이트웨이 시간 제한) / error
    """

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT


CONNECT_ERROR = "Ollama 에 연결할 수 없습니다. Ollama 앱이 실행 중인지 확인하세요."


def _transport_error(e: httpx.TransportError) -> LLMError:
    if isinstance(e, httpx.ConnectError):
        return LLMError(CONNECT_ERROR, kind="connect")
    if isinstance(e, httpx.TimeoutException):
        return LLMError(f"Ollama 응답 시간 초과 ({type(e).__name__})", kind="timeout")
    return LLMError(f"Ollama 연결이 끊겼습니다 ({type(e).__name__})", kind="disconnect")


# 추론 모델이 답 앞에 붙이는 생각 과정
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class OllamaLLM:
    def __init__(
        self,
        model: str,
        url: str = "http://localhost:11434",
        *,
        temperature: float = 0.0,
        num_ctx: int = 16384,
        timeout: float | httpx.Timeout = 300,
        client: httpx.Client | None = None,
    ):
        self.name = model
        # 마지막 호출의 토큰 수. 웹 서버는 여러 스레드가 같은 객체를 쓰므로 스레드마다 따로 둔다
        self._usage = threading.local()
        self.temperature = temperature
        self.num_ctx = num_ctx
        self._client = client or httpx.Client(base_url=url, timeout=timeout)

    def _body(self, messages: list[Message], stream: bool) -> dict:
        return {
            "model": self.name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "think": False,
            # 근거 문서가 길어서 기본 컨텍스트(2048)로는 잘린다
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }

    def _check(self, resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise LLMError(
                f"Ollama 에 {self.name} 모델이 없습니다. 먼저 받으세요: ollama pull {self.name}",
                kind="missing",
            )
        if resp.status_code >= 400:
            resp.read()
            kind = "server" if resp.status_code >= 500 else "error"
            raise LLMError(f"Ollama {resp.status_code}: {resp.text[:300]}", kind=kind)

    def usage(self) -> dict | None:
        """이 스레드에서 마지막으로 부른 chat/stream 의 {"input": n, "output": n}."""
        return getattr(self._usage, "value", None)

    def _record(self, data: dict) -> None:
        if "prompt_eval_count" in data or "eval_count" in data:
            self._usage.value = {
                "input": data.get("prompt_eval_count", 0),
                "output": data.get("eval_count", 0),
            }

    def chat(self, messages: list[Message]) -> str:
        self._usage.value = None
        try:
            resp = self._client.post("/api/chat", json=self._body(messages, False))
        except httpx.TransportError as e:
            raise _transport_error(e) from e
        self._check(resp)
        data = resp.json()
        self._record(data)
        return _THINK_RE.sub("", data["message"]["content"]).strip()

    def stream(self, messages: list[Message]) -> Iterator[str]:
        """답변을 만들어지는 대로 조각씩 돌려준다."""
        self._usage.value = None
        try:
            with self._client.stream("POST", "/api/chat", json=self._body(messages, True)) as resp:
                self._check(resp)
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise LLMError(f"Ollama: {data['error']}")
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if data.get("done"):
                        self._record(data)
                        return
        except httpx.TransportError as e:
            raise _transport_error(e) from e
