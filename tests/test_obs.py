import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from dartrag.answer import Answerer, Message, OllamaLLM
from dartrag.eval.gate import check_release, load_criteria, render_checks
from dartrag.obs import metrics
from dartrag.obs.errors import scrub_event
from dartrag.obs.tracing import LangfuseTracer
from dartrag.web.app import create_app
from dartrag.web.ratelimit import RateLimiter, Rule
from dartrag.web.services import Services
from tests.test_answer import HITS, FakeLLM, FakeRetriever, StreamLLM
from tests.test_web import FakeAnswerer, FakeRepo
from tests.test_web import FakeRetriever as WebRetriever


def value(name, **labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0


def make_client(**kw):
    repo = FakeRepo()

    @contextmanager
    def repo_cm():
        yield repo

    services = Services(repo_cm, lambda r: FakeAnswerer(), lambda r: WebRetriever(), **kw)
    return TestClient(create_app(services))


# --- 지표 ---------------------------------------------------------------------


def test_metrics_endpoint_counts_routes_and_reads_state():
    snap = {
        "jobs": [
            {"name": "ingest", "last_success": datetime.now(UTC) - timedelta(minutes=3)},
            {"name": "feed_poll", "last_success": None, "status": "error"},
        ],
        "ingest_backlog": 4,
        "issues": {"error": 1, "warn": 2},
        "dart_calls_today": 120,
        "dart_daily_limit": 20000,
        "users": 3,
        "eval": {
            "finished_at": datetime.now(UTC),
            "summary": {"overall": {"pass_rate": 0.85}},
            "passed": True,
        },
    }
    client = make_client(ops_snapshot=lambda: snap)
    before = value(
        "dartrag_http_requests_total", method="GET", route="/api/company/{stock}", status="404"
    )
    client.get("/api/company/999999")
    body = client.get("/metrics").text
    after = value(
        "dartrag_http_requests_total", method="GET", route="/api/company/{stock}", status="404"
    )
    assert after == before + 1
    # 경로 대신 라우트 이름으로 센다
    assert "999999" not in body
    assert "dartrag_state_up 1.0" in body
    assert 'dartrag_job_last_failed{job="feed_poll"} 1.0' in body
    assert 'dartrag_job_last_success_age_seconds{job="ingest"}' in body
    assert "dartrag_ingest_backlog 4.0" in body
    assert 'dartrag_data_issues{severity="error"} 1.0' in body
    assert "dartrag_dart_calls_today 120.0" in body
    assert 'dartrag_eval_metric{metric="pass_rate"} 0.85' in body
    assert "dartrag_eval_release_passed 1.0" in body


def test_metrics_survive_state_errors_and_need_token():
    def broken():
        raise RuntimeError("db down")

    client = make_client(ops_snapshot=broken, metrics_token="s3cret")
    assert client.get("/metrics").status_code == 401
    r = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and "dartrag_state_up 0.0" in r.text


def test_answer_metrics_outcomes_and_tokens():
    before = value("dartrag_answers_total", outcome="answered")
    refused = value("dartrag_answers_total", outcome="refused")
    Answerer(FakeRetriever(HITS), FakeLLM("DS 매출은 111조원입니다 [1].")).answer("DS 매출은?")
    Answerer(FakeRetriever(HITS), FakeLLM("x")).answer("삼성전자 지금 사도 돼? 매수 추천해줘")
    assert value("dartrag_answers_total", outcome="answered") == before + 1
    assert value("dartrag_answers_total", outcome="refused") == refused + 1
    assert value("dartrag_answer_stage_seconds_count", stage="retrieve") >= 1


@respx.mock
def test_ollama_reports_token_usage():
    url = "http://ollama:11434"
    respx.post(f"{url}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": "답"}, "prompt_eval_count": 900, "eval_count": 40},
        )
    )
    llm = OllamaLLM("qwen3:8b", url)
    assert llm.usage() is None
    llm.chat([Message("user", "q")])
    assert llm.usage() == {"input": 900, "output": 40}

    lines = [
        {"message": {"content": "답"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 10, "eval_count": 2},
    ]
    respx.post(f"{url}/api/chat").mock(
        return_value=httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))
    )
    list(llm.stream([Message("user", "q")]))
    assert llm.usage() == {"input": 10, "output": 2}


# --- 추적 ---------------------------------------------------------------------


def attrs(span):
    out = {}
    for a in span["attributes"]:
        v = a["value"]
        out[a["key"]] = v.get("stringValue", v.get("intValue", v.get("boolValue")))
    return out


@respx.mock
def test_langfuse_trace_of_streamed_answer():
    route = respx.post("http://lf/api/public/otel/v1/traces").mock(
        return_value=httpx.Response(200, json={})
    )
    tracer = LangfuseTracer("http://lf", "pk", "sk", background=False, release="1.0")
    answerer = Answerer(FakeRetriever(HITS), StreamLLM(""), tracer=tracer)
    events = list(answerer.stream("DS 매출은?", user_id=7, session_id=42))
    assert events[-1][0] == "done"
    tracer.flush()

    request = route.calls[0].request
    assert request.headers["authorization"].startswith("Basic ")
    assert request.headers["content-type"] == "application/json"
    spans = json.loads(request.content)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [s["name"] for s in spans] == ["retrieve", "generate", "answer"]
    retrieve, gen, root = spans
    assert len(root["traceId"]) == 32 and len(root["spanId"]) == 16
    assert "parentSpanId" not in root
    assert retrieve["parentSpanId"] == gen["parentSpanId"] == root["spanId"]
    assert {s["traceId"] for s in spans} == {root["traceId"]}

    r = attrs(root)
    assert r["user.id"] == "7" and r["session.id"] == "42"
    assert r["langfuse.trace.input"] == "DS 매출은?" and r["langfuse.release"] == "1.0"
    assert r["langfuse.trace.output"] == "DS 매출은 111조원입니다 [1]."
    assert json.loads(r["langfuse.observation.metadata"])["cited"] == [1]

    g = attrs(gen)
    assert g["langfuse.observation.type"] == "generation"
    assert g["langfuse.observation.model.name"] == "fake"
    assert g["langfuse.observation.output"] == "DS 매출은 111조원입니다 [1]."
    assert "langfuse.observation.completion_start_time" in g
    assert json.loads(g["langfuse.observation.input"])[0]["role"] == "system"

    hits = json.loads(attrs(retrieve)["langfuse.observation.output"])
    assert [h["chunk_id"] for h in hits] == ["c1", "c2"]
    assert int(root["endTimeUnixNano"]) >= int(root["startTimeUnixNano"])


@respx.mock
def test_langfuse_down_does_not_break_answers():
    respx.post("http://lf/api/public/otel/v1/traces").mock(side_effect=httpx.ConnectError("x"))
    tracer = LangfuseTracer("http://lf", "pk", "sk", background=False)
    result = Answerer(FakeRetriever(HITS), FakeLLM("답 [1]."), tracer=tracer).answer("q?")
    tracer.flush()
    assert result.text == "답 [1]."


def test_langfuse_sampling_off_sends_nothing():
    tracer = LangfuseTracer("http://lf", "pk", "sk", sample_rate=0.0, background=False)
    tracer.trace("answer", input="q").end("a")
    assert tracer._queue.empty()


# --- 오류 수집 ----------------------------------------------------------------


def test_sentry_scrub_removes_private_data():
    event = {
        "request": {
            "url": "https://x/api/ask",
            "data": {"question": "비밀 질문"},
            "cookies": {"dartrag_session": "abc"},
            "headers": {
                "Cookie": "a=b",
                "User-Agent": "ua",
                "X-Telegram-Bot-Api-Secret-Token": "t",
            },
        },
        "user": {"email": "me@example.com"},
        "exception": {
            "values": [
                {
                    "value": "보내기 실패 me@example.com bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    "stacktrace": {"frames": [{"function": "f", "vars": {"password": "pw"}}]},
                }
            ]
        },
        "breadcrumbs": {"values": [{"message": "to you@x.co", "data": {"q": 1}}]},
    }
    out = scrub_event(event)
    req = out["request"]
    assert "data" not in req and "cookies" not in req and "user" not in out
    assert req["headers"]["Cookie"] == "[삭제]" and req["headers"]["User-Agent"] == "ua"
    assert req["headers"]["X-Telegram-Bot-Api-Secret-Token"] == "[삭제]"
    exc = out["exception"]["values"][0]
    assert "example.com" not in exc["value"] and "ABCDEFG" not in exc["value"]
    assert "vars" not in exc["stacktrace"]["frames"][0]
    crumb = out["breadcrumbs"]["values"][0]
    assert crumb["message"] == "to [이메일]" and "data" not in crumb


def test_sentry_scrub_removes_query_secrets():
    url = "https://opendart.fss.or.kr/api/list.json?crtfc_key=abcdef0123456789&page_no=1"
    event = {
        "message": f"HTTP Request: GET {url}",
        "breadcrumbs": {"values": [{"message": "GET /api/alerts/email/verify?token=xyz123"}]},
        "exception": {"values": [{"value": f"Client error for url '{url}'"}]},
    }
    out = scrub_event(event)
    assert "abcdef0123456789" not in out["message"] and "page_no=1" in out["message"]
    assert "crtfc_key=[삭제]" in out["exception"]["values"][0]["value"]
    assert "xyz123" not in out["breadcrumbs"]["values"][0]["message"]


def test_worker_keeps_http_request_logs_quiet(monkeypatch):
    import importlib
    import logging
    import sys

    logging.getLogger("httpx").setLevel(logging.NOTSET)
    monkeypatch.delitem(sys.modules, "dartrag.worker.celery_app", raising=False)
    pytest.importorskip("celery")
    importlib.import_module("dartrag.worker.celery_app")
    # httpx 는 INFO 로 요청 주소(인증키·봇 토큰 포함)를 남긴다
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_init_sentry_off_without_dsn():
    from dartrag.config import Settings
    from dartrag.obs.errors import init_sentry

    assert init_sentry(Settings(_env_file=None, sentry_dsn=""), "api") is False


# --- 요청 한도 ----------------------------------------------------------------


def test_rate_limiter_windows():
    clock = [1000.0]
    limiter = RateLimiter(now=lambda: clock[0])
    rules = [Rule(2, 60, "1분에 2번")]
    assert limiter.hit("ask", "u1", rules) is None
    assert limiter.hit("ask", "u1", rules) is None
    rule, retry = limiter.hit("ask", "u1", rules)
    assert rule.limit == 2 and 0 < retry <= 60
    assert limiter.hit("ask", "u2", rules) is None  # 다른 사람은 따로 센다
    clock[0] += 60
    assert limiter.hit("ask", "u1", rules) is None


def test_rate_limiter_shared_through_redis():
    import fakeredis

    r = fakeredis.FakeRedis()
    a, b = RateLimiter(r), RateLimiter(r)
    rules = [Rule(1, 3600, "1시간에 1번")]
    assert a.hit("heavy", "ip1", rules) is None
    assert b.hit("heavy", "ip1", rules) is not None


def test_ask_rate_limit_returns_429_with_retry_after():
    limits = {"ask": [Rule(2, 60, "1분에 2번")], "heavy": [Rule(1, 3600, "1시간에 1번")]}
    client = make_client(limits=limits)
    body = {"question": "DS 매출은?"}
    assert client.post("/api/ask", json=body).status_code == 200
    assert client.post("/api/ask/stream", json=body).status_code == 200
    r = client.post("/api/ask", json=body)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert "1분에 2번" in r.json()["detail"]
    # 요약을 넣지 않은 PDF 는 한도에 세지 않는다
    for _ in range(3):
        assert client.get("/api/company/005930/report.pdf").status_code != 429


# --- 배포 기준 ----------------------------------------------------------------


def summary(pass_rate=0.96, **over):
    block = {
        "count": 80,
        "pass_rate": pass_rate,
        "retrieval_hit_rate": 0.9,
        "number_recall": 0.9,
        "citation_rate": 0.95,
        "invalid_citation_rate": 0.0,
        "unverified_number_rate": 0.05,
        "latency_p50_s": 20.0,
    } | over
    cats = {c: dict(block, count=20) for c in ("numeric", "text", "unanswerable", "adversarial")}
    return {"overall": block, "by_category": cats}


def test_release_gate_passes_and_fails():
    criteria = load_criteria()
    checks = check_release(summary(), criteria)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert "배포 가능" in render_checks(checks)

    bad = check_release(summary(latency_p50_s=90.0), criteria)
    failed = [c.name for c in bad if not c.ok]
    assert failed == ["전체 latency_p50_s"]
    assert "배포 불가 (기준 1개 미달)" in render_checks(bad)


def test_release_gate_regression_and_missing_category():
    criteria = load_criteria()
    checks = check_release(summary(0.86), criteria, baseline=summary(0.92))
    drop = next(c for c in checks if c.name == "기준선 대비 통과율 하락")
    assert not drop.ok and drop.actual == pytest.approx(0.06)

    s = summary()
    del s["by_category"]["adversarial"]
    assert any(c.name == "adversarial 문항" and not c.ok for c in check_release(s, criteria))


def test_ci_check_baseline(tmp_path):
    from dartrag.eval.gate import ci_check

    path = tmp_path / "baseline.json"
    ok, text = ci_check(path, prompt_version=2)
    assert ok and "건너뜁니다" in text

    path.write_text(json.dumps({"meta": {"prompt": 2}, **summary()}), "utf-8")
    ok, text = ci_check(path, prompt_version=2)
    assert ok and "배포 가능" in text

    ok, text = ci_check(path, prompt_version=3)
    assert not ok and "v2" in text and "v3" in text

    path.write_text(json.dumps({"meta": {"prompt": 2}, **summary(0.5)}), "utf-8")
    assert ci_check(path, prompt_version=2)[0] is False


def test_promote_reviewed_feedback_cases(tmp_path):
    from dartrag.eval import EvalCase, load_cases, save_cases
    from dartrag.eval.service import promote_reviewed

    candidates, target = tmp_path / "cand.jsonl", tmp_path / "manual.jsonl"
    save_cases(target, [EvalCase("m1", "기존 문항", "text", expected_keywords=["x"])])
    save_cases(
        candidates,
        [
            EvalCase("feedback-1", "아직 검수 안 함", "text", source="feedback"),
            EvalCase("feedback-2", "정답 채움", "numeric", expected_numbers=["10조원"]),
            EvalCase("feedback-3", "공시에 없는 질문", "unanswerable"),
        ],
    )
    assert promote_reviewed(candidates, target) == (2, 1)
    assert [c.id for c in load_cases(target)] == ["m1", "feedback-2", "feedback-3"]
    assert [c.id for c in load_cases(candidates)] == ["feedback-1"]
