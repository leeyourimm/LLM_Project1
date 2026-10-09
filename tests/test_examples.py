"""예시 질문 답변 미리 넣기: 화면에서 예시를 눌렀을 때와 같은 캐시 키, 데이터가 바뀐 것만 다시,
실패해도 나머지는 계속, 캐시가 꺼져 있으면 건너뜀. dartrag cache warm 과 작업자."""

import json
from contextlib import contextmanager

import fakeredis
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dartrag import cli
from dartrag.answer import Answerer, LLMError
from dartrag.answer.cache import AnswerCache
from dartrag.answer.examples import example_request, warm
from dartrag.config import DEFAULT_EXAMPLES, Settings
from dartrag.web import Services, create_app
from dartrag.worker import jobs
from tests.test_answer import HITS, FakeLLM, FakeRetriever
from tests.test_web import FakeRepo
from tests.test_worker import JobRepo, make_ctx


class CountingLLM(FakeLLM):
    def __init__(self, reply="DS 매출은 111조원입니다 [1].", fail_on=None):
        super().__init__(reply)
        self.calls = []
        self.fail_on = fail_on

    def chat(self, messages):
        question = messages[-1].content
        self.calls.append(question)
        if self.fail_on and self.fail_on in question:
            raise LLMError("Ollama 에 연결할 수 없습니다.")
        return super().chat(messages)


class Repo(FakeRepo):
    indexed = True

    def has_indexed_filings(self):
        return self.indexed


def make(version=None, **llm_kw):
    version = version or [1]
    llm = CountingLLM(**llm_kw)
    retriever = FakeRetriever(HITS)
    cache = AnswerCache(fakeredis.FakeRedis(), llm.name, lambda: version[0])
    return Answerer(retriever, llm, cache=cache), llm, retriever, version


def test_example_request_matches_new_chat_without_scope():
    companies = Repo().listed_companies()
    question, flt = example_request(DEFAULT_EXAMPLES[0], companies)
    assert question == DEFAULT_EXAMPLES[0] and flt.corp_codes == ["00126380"]
    assert flt.year_from is None and flt.year_to is None
    # 목록에 없는 회사면 범위를 좁히지 않는다
    assert example_request("없는회사 매출은?", companies)[1].corp_codes == []


def test_warm_builds_missing_then_skips_until_data_changes():
    answerer, llm, _, version = make()
    repo = Repo()
    lines = []
    first = warm(answerer, repo, DEFAULT_EXAMPLES, echo=lines.append)
    assert first == {"questions": 3, "built": 3, "fresh": 0, "errors": []}
    assert len(llm.calls) == 3 and lines[0].startswith("새로 넣음")

    again = warm(answerer, repo, DEFAULT_EXAMPLES)
    assert again["built"] == 0 and again["fresh"] == 3 and len(llm.calls) == 3

    version[0] = 2  # 새 공시를 색인해 데이터 버전이 올랐다 → 예전 답은 쓰이지 않는다
    rebuilt = warm(answerer, repo, [*DEFAULT_EXAMPLES, "  "])
    assert rebuilt["built"] == 3 and rebuilt["questions"] == 3 and len(llm.calls) == 6


def test_warmed_answer_is_what_the_chat_page_gets():
    """화면에서 예시를 누르면(새 대화, 범위 지정 없음) 미리 넣은 답이 바로 나간다."""
    answerer, llm, _, _ = make()
    repo = Repo()
    warm(answerer, repo, DEFAULT_EXAMPLES)
    calls = len(llm.calls)

    @contextmanager
    def repo_cm():
        yield repo

    client = TestClient(create_app(Services(repo_cm, lambda r: answerer, None)))
    for q in DEFAULT_EXAMPLES:
        body = client.post("/api/ask", json={"question": q, "stocks": []}).json()
        assert body["cached"] is True and body["answer"].endswith("[1].")
        stream = client.post(
            "/api/ask/stream", json={"question": q, "year_from": None, "year_to": None}
        ).text
        done = [b for b in stream.split("\n\n") if b.startswith("event: done")]
        assert json.loads(done[0].split("data: ", 1)[1])["cached"] is True
    assert len(llm.calls) == calls  # 모델을 다시 부르지 않았다
    # 범위를 직접 고르면 다른 키라 캐시를 쓰지 않는다
    other = client.post("/api/ask", json={"question": DEFAULT_EXAMPLES[1], "stocks": ["005930"]})
    assert other.json()["cached"] is False


def test_warm_keeps_going_after_failures_and_refusals():
    answerer, llm, _, _ = make(fail_on="SK하이닉스")
    questions = [*DEFAULT_EXAMPLES, "삼성전자 지금 사야 할까?"]
    result = warm(answerer, Repo(), questions)
    assert result["built"] == 2 and result["fresh"] == 0
    failed, refused = result["errors"]
    assert failed.startswith("SK하이닉스") and "LLMError" in failed
    assert refused.startswith("삼성전자 지금") and "캐시에 남지 않음" in refused
    # 실패한 것만 다음에 다시 한다
    llm.fail_on = None
    retry = warm(answerer, Repo(), DEFAULT_EXAMPLES)
    assert retry["built"] == 1 and retry["fresh"] == 2


def test_warm_skips_without_cache_or_data():
    answerer, llm, _, _ = make()
    repo = Repo()
    repo.indexed = False
    assert "색인한 공시가 없음" in warm(answerer, repo, DEFAULT_EXAMPLES)["skipped"]
    answerer.cache = None
    assert "답변 캐시가 꺼져" in warm(answerer, Repo(), DEFAULT_EXAMPLES)["skipped"]
    assert llm.calls == []


def test_job_skips_when_cache_is_off():
    ctx = make_ctx(JobRepo())
    ctx.settings = Settings(_env_file=None, answer_cache=False)
    assert "꺼져" in jobs.run_job(ctx, "warm_examples", jobs.warm_examples)["skipped"]
    ctx.settings = Settings(_env_file=None, redis_url="")
    assert "꺼져" in jobs.warm_examples(ctx, JobRepo())["skipped"]


def test_job_uses_configured_questions(monkeypatch):
    answerer, llm, _, _ = make()
    monkeypatch.setattr("dartrag.factory.build_answerer", lambda backends, repo: answerer)
    ctx = make_ctx(Repo())
    ctx.settings = Settings(_env_file=None, example_questions="삼성전자 배당 정책은?")
    result = jobs.warm_examples(ctx, Repo())
    assert result["built"] == 1 and llm.calls and "배당 정책" in llm.calls[0]


def test_cli_cache_warm_without_cache(monkeypatch):
    monkeypatch.setenv("ANSWER_CACHE", "false")
    cli.get_settings.cache_clear()
    try:
        r = CliRunner().invoke(cli.app, ["cache", "warm"])
    finally:
        cli.get_settings.cache_clear()
    assert r.exit_code == 0 and "답변 캐시가 꺼져" in r.output and r.exception is None


def test_cli_cache_warm_redis_down(monkeypatch):
    monkeypatch.setenv("ANSWER_CACHE", "true")
    monkeypatch.setenv("REDIS_URL", "redis://:hunter2@127.0.0.1:1/0")
    cli.get_settings.cache_clear()
    try:
        r = CliRunner().invoke(cli.app, ["cache", "warm"])
    finally:
        cli.get_settings.cache_clear()
    assert r.exit_code == 1 and "Redis 에 연결할 수 없습니다" in r.output
    assert "hunter2" not in r.output and not isinstance(r.exception, ConnectionError)


def test_cli_cache_warm_reports_each_question(monkeypatch):
    redis = fakeredis.FakeRedis()
    ctx = make_ctx(JobRepo(), redis)
    monkeypatch.setenv("ANSWER_CACHE", "true")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(jobs.Context, "from_settings", classmethod(lambda cls, s: ctx))

    def fake_warm(c, r, echo=None):
        echo("새로 넣음  삼성전자 2024년 매출액과 영업이익률은? (1.0초)")
        return {"questions": 3, "built": 1, "fresh": 1, "errors": ["x: LLMError"]}

    monkeypatch.setattr(jobs, "warm_examples", fake_warm)
    cli.get_settings.cache_clear()
    try:
        r = CliRunner().invoke(cli.app, ["cache", "warm"])
        assert r.exit_code == 1  # 실패한 질문이 있으면
        assert "새로 넣음  삼성전자" in r.output
        assert "새로 넣음 1개, 이미 있음 1개, 실패 1개" in r.output
        assert redis.get("dartrag:job:warm_examples") is None  # 끝나면 잠금을 푼다

        # 작업자가 이미 넣고 있으면 겹쳐 돌지 않는다
        redis.set("dartrag:job:warm_examples", "worker", ex=60)
        busy = CliRunner().invoke(cli.app, ["cache", "warm"])
        assert busy.exit_code == 1 and "작업자가 지금" in busy.output
    finally:
        cli.get_settings.cache_clear()
