import json
import re
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from typer.testing import CliRunner

from dartrag import bench, cli
from dartrag.answer import Answerer, LLMError
from dartrag.answer.gateway import LLMGateway
from dartrag.bench import Question, Result, percentile
from dartrag.web.app import create_app
from dartrag.web.ratelimit import Rule
from dartrag.web.services import Services
from tests.test_answer import HITS, FakeRetriever
from tests.test_web import FakeRepo

ROOT = Path(__file__).resolve().parent.parent

# --- 계산 ---------------------------------------------------------------------


def test_percentile_matches_linear_interpolation():
    xs = [float(x) for x in range(1, 11)]
    assert percentile(xs, 50) == 5.5
    assert percentile(xs, 95) == pytest.approx(9.55)  # numpy.percentile(1..10, 95)
    assert percentile(xs, 0) == 1.0 and percentile(xs, 100) == 10.0
    assert percentile(reversed(xs), 50) == 5.5  # 정렬하지 않은 값도
    assert percentile([3.0], 95) == 3.0
    assert percentile([], 50) is None
    assert percentile([1.0, 2.0], 95) == pytest.approx(1.95)


def test_slo_matches_design_doc():
    text = (ROOT / "docs/design.md").read_text("utf-8")
    m = re.search(r"Q&A 첫 토큰 \| (\d+)초 이내, 전체 응답 p95 (\d+)초 이내", text)
    assert m, "docs/design.md 10절의 SLO 문구가 바뀌었으면 bench.py 의 기준도 맞춰 주세요"
    assert (float(m[1]), float(m[2])) == (bench.SLO_FIRST_TOKEN_S, bench.SLO_TOTAL_P95_S)


def test_parse_sse_events():
    lines = [
        ": 주석",
        "event: meta",
        'data: {"conversation_id": 1}',
        "",
        "event: token",
        'data: {"text": "가"}',
        "",
        'data: {"a":',
        "data: 1}",
        "",
        "event: done",
        'data: {"found": true}',
    ]
    assert list(bench.parse_sse(lines)) == [
        ("meta", {"conversation_id": 1}),
        ("token", {"text": "가"}),
        ("message", {"a": 1}),
        ("done", {"found": True}),
    ]


def result(first, total, ok=True, **kw):
    return Result(Question("q"), ok, first, total, **kw)


def test_summarize_compares_with_slo():
    results = [result(0.5 + i / 10, 3.0 + i / 5) for i in range(20)]
    results.append(result(None, 1.0, ok=False, error="요청 한도(429)"))
    results.append(result(0.1, 0.2, cached=True, model="big"))
    results.append(result(0.3, 0.3, found=False, model="small"))
    s = bench.summarize(results, 10.0, concurrency=2)
    assert s["requests"] == 23 and s["ok"] == 22 and s["errors"] == {"요청 한도(429)": 1}
    assert s["cached"] == 1 and s["not_found"] == 1
    assert s["models"] == {"?": 20, "big": 1, "small": 1}
    first = sorted([0.5 + i / 10 for i in range(20)] + [0.1, 0.3])
    assert s["first_token_s"]["p95"] == pytest.approx(percentile(first, 95))
    assert s["first_token_s"]["max"] == pytest.approx(2.4)
    # 첫 글자 p95 는 2초 넘음, 전체 p95 는 8초 이내
    assert s["passed"] == {"first_token": False, "total": True}
    assert s["first_token_within_slo"] == pytest.approx(18 / 22)
    assert s["per_second"] == pytest.approx(2.2)

    report = bench.format_report(s)
    assert "❌ 첫 글자 p95" in report and "✅ 전체 p95" in report
    assert "RATE_ASK_PER_MINUTE=0" in report and "ANSWER_CACHE=false" in report
    assert "실패: 요청 한도(429) 1개" in report

    # 기준을 바꿔 잴 수 있다
    loose = bench.summarize(results, 10.0, slo_first_token=3.0, slo_total_p95=5.0)
    assert loose["passed"] == {"first_token": True, "total": False}


def test_summarize_with_nothing_measured():
    s = bench.summarize([result(None, 0.1, ok=False, error="연결 오류 (ConnectError)")], 0.1)
    assert s["passed"] == {"first_token": False, "total": False}
    assert s["first_token_s"] == {"p50": None, "p95": None, "max": None}
    assert "dartrag serve" in bench.format_report(s)
    few = bench.summarize([result(0.1, 0.2)], 1.0)
    assert "-n 20" in bench.format_report(few)


def test_load_questions_from_eval_files(tmp_path):
    f = tmp_path / "cases.jsonl"
    rows = [
        {"id": "a", "question": "삼성전자 매출은?", "category": "numeric", "stocks": ["005930"]},
        {"id": "b", "question": "지금 사도 돼?", "category": "adversarial"},
        {"id": "c", "question": "없는 내용은?", "category": "unanswerable"},
    ]
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    qs = bench.load_questions([f])
    # 투자 추천·인젝션 문항은 모델에 가지 않아 기본으로 뺀다
    assert [q.case_id for q in qs] == ["a", "c"] and qs[0].stocks == ["005930"]
    assert [q.case_id for q in bench.load_questions([f], ["adversarial"])] == ["b"]
    manual = bench.load_questions([ROOT / "eval/manual.jsonl"])
    assert manual and all(q.text for q in manual)


# --- 가짜 서버에 실제로 보내기 --------------------------------------------------


class SlowLLM:
    """첫 글자까지 first 초, 조각 사이 gap 초 걸리는 모델."""

    def __init__(self, name="big", first=0.05, gap=0.01):
        self.name, self.first, self.gap = name, first, gap

    def stream(self, messages):
        time.sleep(self.first)
        for piece in ["DS 매출은 ", "111조원입니다 ", "[1]."]:
            yield piece
            time.sleep(self.gap)


class MissingLLM:
    name = "big"

    def stream(self, messages):
        raise LLMError("Ollama 에 big 모델이 없습니다. ollama pull big", kind="missing")
        yield  # pragma: no cover


@contextmanager
def serve(llm, **services_kw):
    repo = FakeRepo()

    @contextmanager
    def repo_cm():
        yield repo

    gw = LLMGateway(llm, sleep=lambda s: None)
    answerer = Answerer(FakeRetriever(HITS), gw)
    app = create_app(Services(repo_cm, lambda r: answerer, lambda r: None, **services_kw))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


def test_bench_against_stub_server():
    questions = [Question("삼성전자 DS 매출은?", ["005930"]), Question("배당은?")]
    seen = []
    with serve(SlowLLM(first=0.05)) as url:
        results, elapsed = bench.run_bench(
            url, questions, requests=6, concurrency=3, on_result=lambda i, r: seen.append(i)
        )
    assert seen == [1, 2, 3, 4, 5, 6] and len(results) == 6
    assert all(r.ok and r.status == 200 for r in results), [r.error for r in results]
    for r in results:
        assert 0.05 <= r.first_token_s <= r.total_s
        assert r.tokens == 3 and r.model == "big" and r.found
    s = bench.summarize(results, elapsed, concurrency=3)
    assert s["ok"] == 6 and s["passed"] == {"first_token": True, "total": True}
    assert sorted(q.text for q in (r.question for r in results)).count("배당은?") == 3


def test_bench_reports_rate_limits_and_model_errors():
    limits = {"ask": [Rule(2, 60, "1분에 2번")]}
    with serve(SlowLLM(first=0.0), limits=limits) as url:
        results, _ = bench.run_bench(url, [Question("배당은?")], requests=4)
    assert [r.ok for r in results].count(True) == 2
    assert [r.error for r in results if not r.ok] == ["요청 한도(429)"] * 2
    assert {r.status for r in results if not r.ok} == {429}

    with serve(MissingLLM()) as url:
        (r,), _ = bench.run_bench(url, [Question("배당은?")], requests=1)
    assert not r.ok and "ollama pull big" in r.error and r.status == 200


def test_bench_cannot_connect():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (r,), _ = bench.run_bench(f"http://127.0.0.1:{port}", [Question("q")], requests=1)
    assert not r.ok and r.error.startswith("연결 오류")


def test_bench_cli(tmp_path):
    out = tmp_path / "bench.json"
    with serve(SlowLLM(first=0.02)) as url:
        args = ["bench", "--url", url, "-q", "DS 매출은?", "-s", "005930", "-n", "3"]
        r = CliRunner().invoke(cli.app, [*args, "-c", "2", "--out", str(out)])
        assert r.exit_code == 0, r.output
        assert "[3/3] 첫 글자" in r.output and "✅ 첫 글자 p95" in r.output
        assert "답한 모델: big 3개" in r.output
        saved = json.loads(out.read_text("utf-8"))
        assert saved["summary"]["ok"] == 3 and len(saved["results"]) == 3
        assert saved["results"][0]["question"]["stocks"] == ["005930"]

        # --gate: 기준을 넘으면 실패로 끝난다
        r = CliRunner().invoke(cli.app, [*args, "--gate", "--slo-first-token", "0.001"])
        assert r.exit_code == 1 and "❌ 첫 글자 p95" in r.output

        # 평가 문항 파일에서 질문 읽기
        cases = str(ROOT / "eval/manual.jsonl")
        r = CliRunner().invoke(cli.app, ["bench", "--url", url, "-f", cases, "-n", "2"])
        assert r.exit_code == 0 and "[2/2]" in r.output

    r = CliRunner().invoke(cli.app, ["bench", "-f", str(tmp_path / "none.jsonl")])
    assert r.exit_code == 1 and "질문 파일을 읽지 못했습니다" in r.output
    r = CliRunner().invoke(cli.app, ["bench", "-q", "x", "-n", "0"])
    assert r.exit_code == 1
