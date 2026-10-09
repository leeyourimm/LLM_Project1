import json

import httpx
import pytest
import respx

from dartrag.answer import NOT_FOUND, Answerer, LLMError, Message, OllamaLLM
from dartrag.answer.prompt import build_messages
from dartrag.answer.service import NO_DATA
from dartrag.search import SearchFilter, SearchHit


def hit(cid, body, unit=None):
    return SearchHit(
        cid,
        0.1,
        1,
        None,
        {
            "corp_name": "삼성전자",
            "report_nm": "사업보고서 (2024.12)",
            "section_path": ["II. 사업의 내용", "1. 사업의 개요"],
            "body": body,
            "unit": unit,
        },
    )


HITS = [hit("c1", "DS 부문 매출은 111조원이다.", unit="억원"), hit("c2", "배당은 연 4회 지급한다.")]


class FakeRetriever:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, flt=None, limit=10):
        self.calls.append((query, flt, limit))
        return self.hits[:limit]


class FakeLLM:
    name = "fake"

    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return self.reply


def test_prompt_numbers_sources_with_unit():
    system, user = build_messages("DS 매출은?", HITS)
    assert system.role == "system" and NOT_FOUND in system.content
    assert (
        "[1] 삼성전자 | 사업보고서 (2024.12) | II. 사업의 내용 > 1. 사업의 개요 | 단위: 억원"
        in (user.content)
    )
    assert "[2] 삼성전자" in user.content and user.content.endswith("[질문]\nDS 매출은?")


def test_answer_collects_valid_citations():
    llm = FakeLLM("DS 부문 매출은 111조원입니다 [1]. 배당은 분기마다 줍니다 [2][2].")
    retriever = FakeRetriever(HITS)
    flt = SearchFilter(corp_codes=["00126380"])
    result = Answerer(retriever, llm, top_k=5).answer("질문", flt)
    assert [c.number for c in result.citations] == [1, 2]
    assert result.citations[0].hit.chunk_id == "c1"
    assert result.found and result.warnings == []
    assert retriever.calls == [("질문", flt, 5)]


def test_answer_flags_invalid_or_missing_citations():
    result = Answerer(FakeRetriever(HITS), FakeLLM("매출은 늘었습니다 [3].")).answer("q")
    assert result.citations == []
    assert any("[3]" in w for w in result.warnings)
    assert any("출처 표시가 없는" in w for w in result.warnings)


def test_answer_not_found_paths():
    llm = FakeLLM("unused")
    empty = Answerer(FakeRetriever([]), llm).answer("q")
    assert empty.text == NOT_FOUND and not empty.found and llm.messages is None

    result = Answerer(FakeRetriever(HITS), FakeLLM(NOT_FOUND)).answer("q")
    assert not result.found and result.warnings == []


def test_no_indexed_filings_answers_right_away():
    retriever, llm = FakeRetriever(HITS), FakeLLM("unused")
    empty = Answerer(retriever, llm, has_data=lambda: False)
    result = empty.answer("삼성전자 2024년 영업이익률은?")
    # 검색 모델을 불러오거나 LLM 을 부르지 않고 바로 안내한다
    assert result.text == NO_DATA and not result.found and not result.refused
    assert retriever.calls == [] and llm.messages is None
    events = list(empty.stream("삼성전자 2024년 영업이익률은?"))
    assert [k for k, _ in events] == ["token", "done"] and events[0][1] == NO_DATA
    # 투자 권유처럼 정책상 거절하는 질문은 데이터가 없어도 거절 문구로 답한다
    assert empty.answer("삼성전자 지금 사도 돼?").refused

    ready = Answerer(retriever, FakeLLM("매출은 300조원입니다 [1]."), has_data=lambda: True)
    assert ready.answer("매출은?").found and retriever.calls


URL = "http://ollama:11434"


@respx.mock
def test_ollama_request_and_think_stripping():
    route = respx.post(f"{URL}/api/chat").mock(
        return_value=httpx.Response(
            200, json={"message": {"role": "assistant", "content": "<think>음</think>\n답 [1]"}}
        )
    )
    llm = OllamaLLM("qwen3:8b", URL, num_ctx=8192)
    assert llm.chat([Message("user", "안녕")]) == "답 [1]"
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "qwen3:8b" and body["stream"] is False
    assert body["options"] == {"temperature": 0.0, "num_ctx": 8192}
    assert body["messages"] == [{"role": "user", "content": "안녕"}]


@respx.mock
def test_ollama_errors_are_readable():
    respx.post(f"{URL}/api/chat").mock(return_value=httpx.Response(404, json={"error": "x"}))
    with pytest.raises(LLMError, match="ollama pull qwen3:8b"):
        OllamaLLM("qwen3:8b", URL).chat([Message("user", "q")])

    respx.post(f"{URL}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LLMError, match="Ollama 앱"):
        OllamaLLM("qwen3:8b", URL).chat([Message("user", "q")])


def test_answer_flags_numbers_missing_from_cited_source():
    hits = [
        hit("c1", "| DS | 111,066,000 |", unit="백만원"),
        hit("c2", "배당금 총액은 9조 8,094억원이다."),
    ]
    llm = FakeLLM("DS 매출은 111조원입니다 [1]. 배당 총액은 9조 8,094억원입니다 [1].")
    result = Answerer(FakeRetriever(hits), llm).answer("q")
    # 두 번째 문장의 숫자는 [2]에는 있지만 인용한 [1]에는 없다
    assert result.unverified == ["9조 8,094억원"]
    assert any("확인되지 않은 숫자" in w for w in result.warnings)

    ok = Answerer(FakeRetriever(hits), FakeLLM("배당 총액은 9.8조원입니다 [2].")).answer("q")
    assert ok.unverified == [] and ok.warnings == []


@respx.mock
def test_ollama_stream():
    lines = [
        {"message": {"content": "DS 매출은 "}, "done": False},
        {"message": {"content": "111조원 [1]."}, "done": False},
        {"message": {"content": ""}, "done": True},
    ]
    route = respx.post(f"{URL}/api/chat").mock(
        return_value=httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))
    )
    pieces = list(OllamaLLM("qwen3:8b", URL).stream([Message("user", "q")]))
    assert pieces == ["DS 매출은 ", "111조원 [1]."]
    assert json.loads(route.calls[0].request.content)["stream"] is True

    respx.post(f"{URL}/api/chat").mock(return_value=httpx.Response(404, json={"error": "x"}))
    with pytest.raises(LLMError, match="ollama pull"):
        list(OllamaLLM("qwen3:8b", URL).stream([Message("user", "q")]))
    respx.post(f"{URL}/api/chat").mock(
        return_value=httpx.Response(200, text=json.dumps({"error": "out of memory"}))
    )
    with pytest.raises(LLMError, match="out of memory"):
        list(OllamaLLM("qwen3:8b", URL).stream([Message("user", "q")]))


class StreamLLM(FakeLLM):
    def stream(self, messages):
        self.messages = messages
        yield from ["DS 매출은 ", "111조원입니다 [1]."]


def test_answerer_stream_events():
    events = list(Answerer(FakeRetriever(HITS), StreamLLM("")).stream("DS 매출은?"))
    kinds = [k for k, _ in events]
    assert kinds == ["sources", "token", "token", "done"]
    done = events[-1][1]
    assert done.text == "DS 매출은 111조원입니다 [1]." and done.citations[0].number == 1
    # 스트리밍을 못 하는 모델도 같은 순서로 동작
    events = list(Answerer(FakeRetriever(HITS), FakeLLM("답 [2]")).stream("배당은?"))
    assert [k for k, _ in events] == ["sources", "token", "done"]
    events = list(Answerer(FakeRetriever([]), StreamLLM("")).stream("없는 것"))
    assert events[0][0] == "done" and events[0][1].found is False
