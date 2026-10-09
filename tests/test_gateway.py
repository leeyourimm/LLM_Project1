import threading
import time

import httpx
import pytest
import respx

from dartrag.answer import Answerer, LLMError, Message, OllamaLLM
from dartrag.answer.gateway import LLMGateway, answered_by, classify
from dartrag.config import Settings
from dartrag.factory import build_llm
from dartrag.obs import metrics
from dartrag.obs.tracing import LangfuseTracer
from tests.test_answer import HITS, FakeLLM, FakeRetriever

MSGS = [Message("user", "q")]


def value(name, **labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0


class ScriptLLM:
    """부를 때마다 정해 둔 동작을 차례로 한다. 조각 목록이면 내보내고, 예외면 올린다.

    조각 목록 안의 예외는 그 자리에서 올리고(앞 조각을 낸 뒤 끊김), threading.Event 는
    풀릴 때까지 기다린다 (첫 글자가 늦는 모델). 마지막 동작은 계속 되풀이한다."""

    def __init__(self, name, *script):
        self.name = name
        self.script = list(script)
        self.calls = 0
        self.sent: list[str] = []
        self.closed = threading.Event()
        self._usage = threading.local()

    def stream(self, messages):
        self.calls += 1
        step = self.script[min(self.calls, len(self.script)) - 1]
        self._usage.value = None
        if isinstance(step, BaseException):
            raise step
        try:
            for piece in step:
                if isinstance(piece, BaseException):
                    raise piece
                if isinstance(piece, threading.Event):
                    piece.wait(5)
                    continue
                self.sent.append(piece)
                yield piece
            # Ollama 처럼 토큰 수를 이 스레드에만 적는다
            self._usage.value = {"input": 10, "output": len(step)}
        finally:
            self.closed.set()

    def usage(self):
        return getattr(self._usage, "value", None)


def gateway(primary, fallback=None, **kw):
    sleeps: list[float] = []
    kw.setdefault("sleep", sleeps.append)
    kw.setdefault("jitter", lambda: 1.0)
    gw = LLMGateway(primary, fallback, **kw)
    gw.sleeps = sleeps
    return gw


def connect_error():
    return LLMError("Ollama 에 연결할 수 없습니다.", kind="connect")


def missing(name):
    return LLMError(f"Ollama 에 {name} 모델이 없습니다. ollama pull {name}", kind="missing")


# --- 재시도 -------------------------------------------------------------------


def test_retries_transient_errors_with_backoff():
    llm = ScriptLLM("big", connect_error(), LLMError("Ollama 503", kind="server"), ["답 [1]"])
    gw = gateway(llm)
    connect = value("dartrag_llm_failures_total", model="big", kind="connect")
    ok = value("dartrag_llm_requests_total", model="big", outcome="ok")
    assert gw.chat(MSGS) == "답 [1]"
    assert llm.calls == 3 and gw.sleeps == [0.5, 1.0]
    call = gw.last_call()
    assert call.model == "big" and call.attempts == 3 and not call.fallback
    assert call.errors == ["big: connect", "big: server"]
    assert value("dartrag_llm_failures_total", model="big", kind="connect") == connect + 1
    assert value("dartrag_llm_requests_total", model="big", outcome="ok") == ok + 1
    assert gw.usage() == {"input": 10, "output": 1}


def test_retries_are_bounded_and_backoff_is_capped():
    llm = ScriptLLM("big", connect_error())
    gw = gateway(llm, retries=4, backoff=1.0, max_backoff=3.0)
    errors = value("dartrag_llm_requests_total", model="big", outcome="error")
    with pytest.raises(LLMError, match="연결할 수 없습니다") as e:
        gw.chat(MSGS)
    assert e.value.kind == "connect"
    assert llm.calls == 5 and gw.sleeps == [1.0, 2.0, 3.0, 3.0]
    assert value("dartrag_llm_requests_total", model="big", outcome="error") == errors + 1
    # 기다리는 시간은 절반~전부 사이에서 흩어진다 (여러 요청이 한꺼번에 다시 몰리지 않게)
    jittered = gateway(ScriptLLM("big", connect_error()), retries=1, jitter=lambda: 0.0)
    with pytest.raises(LLMError):
        jittered.chat(MSGS)
    assert jittered.sleeps == [0.25]


def test_no_retry_when_model_is_missing():
    llm = ScriptLLM("big", missing("big"))
    gw = gateway(llm)
    with pytest.raises(LLMError, match="ollama pull big") as e:
        gw.chat(MSGS)
    assert e.value.kind == "missing" and llm.calls == 1 and gw.sleeps == []


def test_raw_transport_errors_are_classified_and_retried():
    request = httpx.Request("POST", "http://x/api/chat")
    assert classify(httpx.ConnectError("refused")) == "connect"
    assert classify(httpx.ReadTimeout("slow")) == "timeout"
    assert classify(httpx.ConnectTimeout("slow")) == "timeout"
    assert classify(httpx.RemoteProtocolError("cut")) == "disconnect"
    for status, kind in ((503, "server"), (400, "error")):
        err = httpx.HTTPStatusError("x", request=request, response=httpx.Response(status))
        assert classify(err) == kind
    assert classify(KeyError("x")) == "error"

    llm = ScriptLLM("big", httpx.ReadTimeout("slow"), ["답"])
    gw = gateway(llm)
    assert gw.chat(MSGS) == "답" and llm.calls == 2
    # 그대로 올라온 연결 오류도 화면에 보여 줄 LLMError 로 바꿔 올린다
    with pytest.raises(LLMError) as e:
        gateway(ScriptLLM("big", httpx.ConnectError("refused")), retries=0).chat(MSGS)
    assert e.value.kind == "connect"


def test_unexpected_errors_are_not_retried_or_masked():
    llm = ScriptLLM("big", KeyError("message"))
    with pytest.raises(KeyError):
        gateway(llm).chat(MSGS)
    assert llm.calls == 1
    # 대체 모델이 있으면 그쪽이 답한다
    backup = ScriptLLM("small", ["작은 답"])
    assert gateway(ScriptLLM("big", KeyError("m")), backup).chat(MSGS) == "작은 답"


# --- 대체 모델 ----------------------------------------------------------------


def test_fallback_when_primary_fails():
    primary, backup = ScriptLLM("big", missing("big")), ScriptLLM("small", ["작은 ", "답"])
    gw = gateway(primary, backup)
    before = value("dartrag_llm_requests_total", model="small", outcome="fallback")
    assert list(gw.stream(MSGS)) == ["작은 ", "답"]
    call = gw.last_call()
    assert call.model == "small" and call.fallback and call.requested == "big"
    assert call.errors == ["big: missing"] and primary.calls == 1
    assert gw.name == "big" and answered_by(gw) == "small"
    assert value("dartrag_llm_requests_total", model="small", outcome="fallback") == before + 1
    # 대체 모델도 일시적 오류면 다시 시도한다
    backup = ScriptLLM("small", connect_error(), ["답"])
    gw = gateway(ScriptLLM("big", connect_error()), backup, retries=1)
    assert gw.chat(MSGS) == "답" and gw.last_call().attempts == 4


def test_all_models_failing_reports_each_reason():
    gw = gateway(ScriptLLM("big", missing("big")), ScriptLLM("small", missing("small")))
    with pytest.raises(LLMError) as e:
        gw.chat(MSGS)
    assert "ollama pull big" in str(e.value) and "ollama pull small" in str(e.value)
    # 같은 문구는 한 번만
    gw = gateway(ScriptLLM("big", connect_error()), ScriptLLM("small", connect_error()))
    with pytest.raises(LLMError) as e:
        gw.chat(MSGS)
    assert str(e.value) == "Ollama 에 연결할 수 없습니다."
    assert gw.last_call().attempts == 6


def test_fallback_after_first_token_timeout():
    release = threading.Event()
    primary = ScriptLLM("big", [release, "늦은 답"])
    backup = ScriptLLM("small", ["빠른 답"])
    gw = gateway(primary, backup, first_token_timeout=0.05)
    started = time.monotonic()
    assert gw.chat(MSGS) == "빠른 답"
    assert time.monotonic() - started < 2
    call = gw.last_call()
    assert call.fallback and call.errors == ["big: deadline"]
    # 느린 모델에 같은 요청을 다시 보내지 않는다
    assert primary.calls == 1 and gw.sleeps == []
    # 포기한 요청은 첫 조각이 오는 즉시 연결을 닫는다
    release.set()
    assert primary.closed.wait(2)


def test_first_token_timeout_only_applies_when_there_is_a_fallback():
    slow = threading.Event()
    threading.Timer(0.1, slow.set).start()
    gw = gateway(ScriptLLM("big", [slow, "답"]), first_token_timeout=0.01)
    assert gw.chat(MSGS) == "답"


def test_total_timeout_and_no_fallback_after_text_was_sent():
    stalled = threading.Event()
    primary = ScriptLLM("big", ["앞부분 ", stalled, "뒷부분"])
    backup = ScriptLLM("small", ["다른 답"])
    gw = gateway(primary, backup, timeout=0.1, first_token_timeout=5)
    got = []
    with pytest.raises(LLMError, match="0.1초를 넘었습니다") as e:
        for piece in gw.stream(MSGS):
            got.append(piece)
    assert e.value.kind == "deadline"
    assert got == ["앞부분 "] and backup.calls == 0 and primary.calls == 1
    stalled.set()


def test_stream_never_retries_after_first_piece():
    # 앞 조각을 낸 뒤 연결이 끊겨도(일시적 오류) 다시 보내면 글이 겹치므로 그대로 올린다
    primary = ScriptLLM("big", ["앞부분 ", LLMError("끊김", kind="disconnect")], ["새 답"])
    backup = ScriptLLM("small", ["다른 답"])
    gw = gateway(primary, backup)
    got = []
    with pytest.raises(LLMError, match="끊김"):
        for piece in gw.stream(MSGS):
            got.append(piece)
    assert got == ["앞부분 "]
    assert primary.calls == 1 and backup.calls == 0 and gw.sleeps == []
    assert gw.last_call().errors == ["big: disconnect"]


def test_closing_the_stream_stops_the_model():
    # 화면을 닫아 답변을 그만 받으면 모델 쪽 연결도 닫아 생성을 멈춘다
    gate = threading.Event()
    primary = ScriptLLM("big", ["하나", gate, "둘", "셋"])
    stream = gateway(primary).stream(MSGS)
    assert next(stream) == "하나"
    stream.close()
    gate.set()
    assert primary.closed.wait(2)
    assert primary.sent == ["하나", "둘"]


def test_chat_strips_thinking_and_works_with_non_streaming_models():
    gw = gateway(ScriptLLM("big", ["<think>음</think>", "\n답 [1] "]))
    assert gw.chat(MSGS) == "답 [1]"
    plain = FakeLLM("답 [2]")
    assert gateway(plain).chat(MSGS) == "답 [2]" and plain.messages == MSGS


def test_chat_timeout_with_non_streaming_model():
    class Slow(FakeLLM):
        name = "slow"

        def chat(self, messages):
            time.sleep(0.3)
            return "늦은 답"

    with pytest.raises(LLMError, match="답변이 0.05초를 넘었습니다"):
        gateway(Slow("x"), timeout=0.05).chat(MSGS)


def test_usage_and_last_call_are_per_thread():
    gw = gateway(ScriptLLM("big", ["a", "b"]))
    gw.chat(MSGS)
    seen = {}

    def other():
        seen["usage"], seen["call"] = gw.usage(), gw.last_call()

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen == {"usage": None, "call": None}
    assert gw.usage() == {"input": 10, "output": 2}


# --- Ollama 오류 종류 ----------------------------------------------------------

URL = "http://ollama:11434"


@respx.mock
def test_ollama_error_kinds():
    route = respx.post(f"{URL}/api/chat")
    llm = OllamaLLM("qwen3:8b", URL)
    cases = [
        (httpx.Response(404, json={"error": "x"}), "missing"),
        (httpx.Response(500, text="runner terminated"), "server"),
        (httpx.Response(400, text="bad"), "error"),
        (httpx.ConnectError("refused"), "connect"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.RemoteProtocolError("cut"), "disconnect"),
    ]
    for outcome, kind in cases:
        if isinstance(outcome, Exception):
            route.mock(side_effect=outcome)
        else:
            route.mock(return_value=outcome)
        for call in (lambda: llm.chat(MSGS), lambda: list(llm.stream(MSGS))):
            with pytest.raises(LLMError) as e:
                call()
            assert e.value.kind == kind, (outcome, kind)
    assert LLMError("x", kind="server").transient and not LLMError("x", kind="missing").transient


# --- 조립과 답변기 -------------------------------------------------------------


def test_build_llm_wraps_ollama_with_settings():
    gw = build_llm(Settings(_env_file=None))
    assert isinstance(gw, LLMGateway) and gw.name == "qwen3:8b" and gw.fallback is None
    assert gw.timeout == 300 and gw.retries == 2 and gw.backoff == 0.5
    timeout = gw.primary._client.timeout
    assert timeout.connect == 10 and timeout.read == 330

    s = Settings(
        _env_file=None,
        llm_fallback_model="qwen3:1.7b",
        llm_timeout=60,
        llm_first_token_timeout=8,
        llm_retries=1,
    )
    gw = build_llm(s)
    assert gw.fallback.name == "qwen3:1.7b" and gw.first_token_timeout == 8
    assert gw.timeout == 60 and gw.retries == 1
    # 평가는 기본 모델만 잰다. 기본 모델과 같은 이름이면 대체 모델로 쓰지 않는다
    assert build_llm(s, fallback=False).fallback is None
    assert build_llm(Settings(_env_file=None, llm_fallback_model="qwen3:8b")).fallback is None
    assert build_llm(Settings(_env_file=None, llm_timeout=0)).timeout is None


class SpyCache:
    def __init__(self):
        self.puts = []

    def get(self, question, flt):
        return None

    def put(self, question, flt, answer):
        self.puts.append(question)


def test_answerer_records_fallback_model_and_skips_cache():
    def make(primary):
        gw = gateway(primary, ScriptLLM("small", ["DS 매출은 111조원입니다 [1]."]))
        cache = SpyCache()
        return Answerer(FakeRetriever(HITS), gw, cache=cache), cache

    answerer, cache = make(ScriptLLM("big", missing("big")))
    tokens = value("dartrag_llm_tokens_total", model="small", kind="input")
    result = answerer.answer("DS 매출은?")
    assert result.model == "small" and result.citations[0].number == 1
    # 대체 모델의 답은 기본 모델 이름의 캐시에 넣지 않는다
    assert cache.puts == []
    assert value("dartrag_llm_tokens_total", model="small", kind="input") == tokens + 10

    events = list(answerer.stream("DS 매출은?"))
    assert events[-1][1].model == "small" and cache.puts == []

    answerer, cache = make(ScriptLLM("big", ["DS 매출은 111조원입니다 [1]."]))
    assert answerer.answer("DS 매출은?").model == "big" and cache.puts == ["DS 매출은?"]
    # 게이트웨이가 아닌 모델도 그대로 쓴다
    plain = Answerer(FakeRetriever(HITS), FakeLLM("답 [1]."))
    assert plain.answer("q").model == "fake"


@respx.mock
def test_langfuse_generation_names_the_model_that_answered():
    import json

    from tests.test_obs import attrs

    route = respx.post("http://lf/api/public/otel/v1/traces").mock(
        return_value=httpx.Response(200, json={})
    )
    tracer = LangfuseTracer("http://lf", "pk", "sk", background=False)
    gw = gateway(ScriptLLM("big", connect_error()), ScriptLLM("small", ["답 [1]."]), retries=1)
    list(Answerer(FakeRetriever(HITS), gw, tracer=tracer).stream("DS 매출은?"))
    tracer.flush()
    spans = json.loads(route.calls[0].request.content)["resourceSpans"][0]["scopeSpans"][0]
    gen = attrs(next(s for s in spans["spans"] if s["name"] == "generate"))
    assert gen["langfuse.observation.model.name"] == "small"
    meta = json.loads(gen["langfuse.observation.metadata"])
    assert meta == {
        "requested_model": "big",
        "fallback": True,
        "attempts": 3,
        "errors": ["big: connect", "big: connect"],
    }


def test_diff_summary_made_by_fallback_is_saved_under_its_name():
    from dartrag.changes.summary import latest_digest
    from tests.test_diff_summary import LLM_OUT, Repo

    repo = Repo()
    primary = ScriptLLM("big", missing("big"), [LLM_OUT])
    gw = gateway(primary, ScriptLLM("small", [LLM_OUT]))
    digest = latest_digest(repo, "00126380", gw)
    assert digest.model == "small"
    assert next(iter(repo.saved.values()))["model"] == "small"
    # 기본 모델이 돌아오면 다시 만든다
    again = latest_digest(repo, "00126380", gw)
    assert again.model == "big" and primary.calls == 2
