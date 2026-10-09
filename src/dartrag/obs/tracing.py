"""LLM 호출 추적 (Langfuse).

질문 하나가 검색 → 답변 생성으로 이어지는 과정을 Langfuse 에 남겨, 느린 단계나
잘못된 근거로 만든 답변을 화면에서 찾아볼 수 있게 한다.

LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 를 넣을 때만 켜진다.
질문과 근거 원문이 Langfuse 로 가므로 직접 띄운 Langfuse(infra 의 langfuse 프로필)를 쓴다.

SDK 없이 Langfuse 의 OpenTelemetry 수집 주소(/api/public/otel/v1/traces)에 OTLP JSON 으로
보낸다. 질문 하나가 trace 하나이고, 그 아래에 검색(span)과 답변 생성(generation)이 붙는다.
보낼 내용을 큐에 쌓아 두고 별도 스레드가 묶어서 보내므로 답변 속도에 영향을 주지 않고,
Langfuse 가 꺼져 있어도 답변은 그대로 나간다.
"""

import atexit
import json
import logging
import queue
import random
import threading
import uuid
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

MAX_QUEUE = 2000
BATCH = 50
FLUSH_SECONDS = 2.0
MAX_TEXT = 20_000  # 한 항목에 넣는 글자 수 (근거 원문이 길다)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _clip(value):
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "…"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    return value


class Trace:
    """추적하지 않을 때 쓰는 빈 구현. LangfuseTrace 가 같은 메서드를 채운다."""

    id: str | None = None

    def span(self, name: str, start: datetime, end: datetime, **fields) -> None:
        pass

    def generation(self, name: str, start: datetime, end: datetime, **fields) -> None:
        pass

    def end(self, output=None, metadata: dict | None = None) -> None:
        pass


class Tracer:
    enabled = False

    def trace(self, name: str, *, input=None, user_id=None, session_id=None, metadata=None):
        return Trace()

    def flush(self) -> None:
        pass


NOOP = Tracer()


def _nanos(t: datetime) -> str:
    return str(int(t.timestamp() * 1_000_000_000))


def _attr(key: str, value) -> dict | None:
    """OTLP 속성 하나. 문자열이 아닌 값은 JSON 문자열로 넣는다 (Langfuse 가 다시 풀어 읽음)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if not isinstance(value, str):
        value = json.dumps(_clip(value), ensure_ascii=False, default=str)
    return {"key": key, "value": {"stringValue": _clip(value)}}


class LangfuseTrace(Trace):
    def __init__(self, tracer: "LangfuseTracer", name: str, attrs: dict):
        self.tracer = tracer
        self.id = uuid.uuid4().hex  # 32자리
        self.root = uuid.uuid4().hex[:16]
        self.name = name
        self.started = _now()
        self.attrs = attrs

    def _child(self, name, start, end, attrs: dict, error: bool = False) -> None:
        self.tracer.emit(
            {
                "traceId": self.id,
                "spanId": uuid.uuid4().hex[:16],
                "parentSpanId": self.root,
                "name": name,
                "startTimeUnixNano": _nanos(start),
                "endTimeUnixNano": _nanos(end),
                "attributes": attrs,
                "status": {"code": 2 if error else 1},
            }
        )

    def span(self, name, start, end, *, input=None, output=None, metadata=None):
        self._child(
            name,
            start,
            end,
            {
                "langfuse.observation.type": "span",
                "langfuse.observation.input": input,
                "langfuse.observation.output": output,
                "langfuse.observation.metadata": metadata,
            },
        )

    def generation(
        self,
        name,
        start,
        end,
        *,
        model: str,
        input=None,
        output=None,
        usage: dict | None = None,
        first_token: datetime | None = None,
        metadata=None,
        level: str | None = None,
    ):
        self._child(
            name,
            start,
            end,
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": model,
                "langfuse.observation.input": input,
                "langfuse.observation.output": output,
                "langfuse.observation.usage_details": usage,
                "langfuse.observation.completion_start_time": (
                    _iso(first_token) if first_token else None
                ),
                "langfuse.observation.metadata": metadata,
                "langfuse.observation.level": level,
            },
            error=level == "ERROR",
        )

    def end(self, output=None, metadata=None):
        error = bool(metadata and metadata.get("error"))
        self.tracer.emit(
            {
                "traceId": self.id,
                "spanId": self.root,
                "name": self.name,
                "startTimeUnixNano": _nanos(self.started),
                "endTimeUnixNano": _nanos(_now()),
                "attributes": {
                    **self.attrs,
                    "langfuse.trace.output": output,
                    "langfuse.observation.output": output,
                    "langfuse.observation.metadata": metadata,
                    "langfuse.observation.level": "ERROR" if error else None,
                },
                "status": {"code": 2 if error else 1},
            }
        )


class LangfuseTracer(Tracer):
    enabled = True

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        *,
        sample_rate: float = 1.0,
        release: str | None = None,
        environment: str | None = None,
        client: httpx.Client | None = None,
        background: bool = True,
    ):
        self.sample_rate = sample_rate
        self.release = release
        self.environment = environment
        self._client = client or httpx.Client(
            base_url=host.rstrip("/"), auth=(public_key, secret_key), timeout=10
        )
        self._queue: queue.Queue = queue.Queue(MAX_QUEUE)
        self._dropped = 0
        self._lock = threading.Lock()
        if background:
            threading.Thread(target=self._loop, name="langfuse", daemon=True).start()
            atexit.register(self.flush)

    def trace(self, name, *, input=None, user_id=None, session_id=None, metadata=None):
        if self.sample_rate < 1 and random.random() >= self.sample_rate:
            return Trace()
        attrs = {
            "langfuse.trace.name": name,
            "langfuse.trace.input": input,
            "langfuse.observation.input": input,
            "langfuse.trace.metadata": metadata,
            "user.id": str(user_id) if user_id is not None else None,
            "session.id": str(session_id) if session_id is not None else None,
            "langfuse.release": self.release,
            "langfuse.environment": self.environment,
        }
        return LangfuseTrace(self, name, attrs)

    def emit(self, span: dict) -> None:
        span["attributes"] = [
            a for k, v in span["attributes"].items() if (a := _attr(k, v)) is not None
        ]
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            # Langfuse 가 오래 멈춰 있으면 새로 들어온 것을 버린다
            self._dropped += 1

    def _drain(self) -> list[dict]:
        batch = []
        while len(batch) < BATCH:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def flush(self) -> None:
        with self._lock:
            while batch := self._drain():
                self._send(batch)

    def _send(self, spans: list[dict]) -> None:
        from dartrag import __version__

        body = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_attr("service.name", "dartrag")]},
                    "scopeSpans": [
                        {"scope": {"name": "dartrag", "version": __version__}, "spans": spans}
                    ],
                }
            ]
        }
        try:
            resp = self._client.post("/api/public/otel/v1/traces", json=body)
            if resp.status_code >= 400:
                # 응답 본문에는 보낸 내용이 섞일 수 있어 남기지 않는다
                log.warning("Langfuse 전송 실패: HTTP %s", resp.status_code)
        except httpx.HTTPError as e:
            log.warning("Langfuse 에 연결할 수 없음: %s", type(e).__name__)

    def _loop(self) -> None:
        import time

        while True:
            time.sleep(FLUSH_SECONDS)
            try:
                self.flush()
            except Exception as e:  # noqa: BLE001 - 추적 실패로 스레드가 죽으면 안 된다
                log.warning("Langfuse 전송 스레드 오류: %s", e)
            if self._dropped:
                log.warning("Langfuse 큐가 가득 차 %d건을 버림", self._dropped)
                self._dropped = 0


_tracer: Tracer | None = None


def get_tracer(settings) -> Tracer:
    """설정에 Langfuse 키가 있으면 LangfuseTracer, 없으면 아무것도 하지 않는 Tracer."""
    global _tracer
    if _tracer is None:
        if settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key:
            from dartrag import __version__

            _tracer = LangfuseTracer(
                settings.langfuse_host,
                settings.langfuse_public_key,
                settings.langfuse_secret_key,
                sample_rate=settings.langfuse_sample_rate,
                release=__version__,
                environment=settings.environment,
            )
        else:
            _tracer = NOOP
    return _tracer
