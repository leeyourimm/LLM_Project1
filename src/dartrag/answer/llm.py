"""답변 생성용 LLM.

기본은 내 컴퓨터에서 돌리는 Ollama 로컬 모델이다. 다른 LLM 으로 바꿀 때는
LLM 프로토콜(chat)만 구현하면 된다.
"""

import json
import re
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


class LLMError(RuntimeError):
    pass


CONNECT_ERROR = "Ollama 에 연결할 수 없습니다. Ollama 앱이 실행 중인지 확인하세요."

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
        timeout: float = 300,
        client: httpx.Client | None = None,
    ):
        self.name = model
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
                f"Ollama 에 {self.name} 모델이 없습니다. 먼저 받으세요: ollama pull {self.name}"
            )
        if resp.status_code >= 400:
            resp.read()
            raise LLMError(f"Ollama {resp.status_code}: {resp.text[:300]}")

    def chat(self, messages: list[Message]) -> str:
        try:
            resp = self._client.post("/api/chat", json=self._body(messages, False))
        except httpx.ConnectError as e:
            raise LLMError(CONNECT_ERROR) from e
        self._check(resp)
        return _THINK_RE.sub("", resp.json()["message"]["content"]).strip()

    def stream(self, messages: list[Message]) -> Iterator[str]:
        """답변을 만들어지는 대로 조각씩 돌려준다."""
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
                        return
        except httpx.ConnectError as e:
            raise LLMError(CONNECT_ERROR) from e
