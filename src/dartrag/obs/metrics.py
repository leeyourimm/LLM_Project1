"""Prometheus 지표.

API 서버의 /metrics 에서 내보낸다. 요청 수·지연, 답변 단계별 시간, 토큰 수처럼
요청할 때 생기는 값은 바로 세고, 작업자 상태·처리 대기·데이터 오류·OpenDART 사용량처럼
DB 와 Redis 에 있는 값은 Prometheus 가 가져갈 때 읽는다 (StateCollector).
"""

import logging
import time
from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily

log = logging.getLogger(__name__)

REGISTRY = CollectorRegistry(auto_describe=True)

# 사람이 기다리는 시간 단위에 맞춘 구간 (LLM 답변은 수십 초까지 걸린다)
SLOW_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 60, 120, 300)

HTTP_REQUESTS = Counter(
    "dartrag_http_requests",
    "API 요청 수",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_SECONDS = Histogram(
    "dartrag_http_request_seconds",
    "API 응답 시간 (스트리밍은 응답 시작까지)",
    ["method", "route"],
    buckets=SLOW_BUCKETS,
    registry=REGISTRY,
)
ANSWERS = Counter(
    "dartrag_answers",
    "답변 결과 (answered, not_found, refused, cached, error)",
    ["outcome"],
    registry=REGISTRY,
)
ANSWER_STAGE_SECONDS = Histogram(
    "dartrag_answer_stage_seconds",
    "답변 단계별 시간 (retrieve, first_token, generate, total)",
    ["stage"],
    buckets=SLOW_BUCKETS,
    registry=REGISTRY,
)
ANSWER_WARNINGS = Counter(
    "dartrag_answer_warnings",
    "검증 경고가 붙은 답변 (citation: 출처 표시 문제, number: 확인 안 된 숫자)",
    ["kind"],
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "dartrag_llm_tokens",
    "LLM 토큰 수 (input, output)",
    ["model", "kind"],
    registry=REGISTRY,
)
FEEDBACK = Counter(
    "dartrag_feedback",
    "답변 평가 (up, down)",
    ["rating"],
    registry=REGISTRY,
)
RATE_LIMITED = Counter(
    "dartrag_rate_limited",
    "요청 한도에 걸린 수",
    ["scope"],
    registry=REGISTRY,
)


def record_answer(result, timings: dict[str, float], usage: dict | None, model: str) -> None:
    """Answer 하나를 지표에 반영한다. timings: 단계 → 초."""
    if result.refused:
        outcome = "refused"
    elif result.cached:
        outcome = "cached"
    elif not result.found:
        outcome = "not_found"
    else:
        outcome = "answered"
    ANSWERS.labels(outcome).inc()
    for stage, seconds in timings.items():
        ANSWER_STAGE_SECONDS.labels(stage).observe(seconds)
    if any("출처" in w for w in result.warnings):
        ANSWER_WARNINGS.labels("citation").inc()
    if result.unverified:
        ANSWER_WARNINGS.labels("number").inc()
    if usage:
        for kind in ("input", "output"):
            if usage.get(kind):
                LLM_TOKENS.labels(model, kind).inc(usage[kind])


class StateCollector:
    """Prometheus 가 가져갈 때마다 DB·Redis 의 현재 상태를 읽는 수집기.

    snapshot() 은 {"jobs": [...], "ingest_backlog": n, "issues": {...},
    "dart_calls_today": n, "users": n} 형태를 돌려준다. 실패하면 그 회차는 건너뛴다.
    """

    def __init__(self, snapshot: Callable[[], dict]):
        self.snapshot = snapshot

    def describe(self):
        return []

    def collect(self):
        try:
            s = self.snapshot()
        except Exception as e:  # noqa: BLE001 - 지표 때문에 /metrics 가 실패하면 안 된다
            log.warning("상태 지표를 읽지 못함: %s", e)
            up = GaugeMetricFamily("dartrag_state_up", "DB·Redis 상태 읽기 성공 여부")
            up.add_metric([], 0)
            yield up
            return
        up = GaugeMetricFamily("dartrag_state_up", "DB·Redis 상태 읽기 성공 여부")
        up.add_metric([], 1)
        yield up

        now = time.time()
        last_ok = GaugeMetricFamily(
            "dartrag_job_last_success_age_seconds",
            "작업이 마지막으로 성공한 뒤 지난 시간",
            labels=["job"],
        )
        last_status = GaugeMetricFamily(
            "dartrag_job_last_failed",
            "작업의 마지막 실행이 실패했으면 1",
            labels=["job"],
        )
        for j in s.get("jobs", []):
            if j.get("last_success"):
                last_ok.add_metric([j["name"]], now - j["last_success"].timestamp())
            last_status.add_metric([j["name"]], 1 if j.get("status") == "error" else 0)
        yield last_ok
        yield last_status

        backlog = GaugeMetricFamily("dartrag_ingest_backlog", "아직 처리하지 않은 정기보고서 수")
        backlog.add_metric([], s.get("ingest_backlog", 0))
        yield backlog

        issues = GaugeMetricFamily(
            "dartrag_data_issues", "재무 데이터 검증에 걸린 항목 수", labels=["severity"]
        )
        for severity, n in s.get("issues", {}).items():
            issues.add_metric([severity], n)
        yield issues

        if "dart_calls_today" in s:
            calls = GaugeMetricFamily(
                "dartrag_dart_calls_today", "오늘(한국 시간) OpenDART 호출 수"
            )
            calls.add_metric([], s["dart_calls_today"])
            yield calls
        if "dart_daily_limit" in s:
            limit = GaugeMetricFamily("dartrag_dart_daily_limit", "OpenDART 하루 호출 한도")
            limit.add_metric([], s["dart_daily_limit"])
            yield limit

        if ev := s.get("eval"):
            overall = (ev.get("summary") or {}).get("overall") or {}
            rate = GaugeMetricFamily(
                "dartrag_eval_metric", "가장 최근 평가 결과 (전체)", labels=["metric"]
            )
            for key in ("pass_rate", "retrieval_hit_rate", "citation_rate", "number_recall"):
                if overall.get(key) is not None:
                    rate.add_metric([key], overall[key])
            yield rate
            passed = GaugeMetricFamily(
                "dartrag_eval_release_passed", "가장 최근 평가가 배포 기준을 넘었으면 1"
            )
            passed.add_metric([], 1 if ev.get("passed") else 0)
            yield passed
            age = GaugeMetricFamily("dartrag_eval_age_seconds", "가장 최근 평가 뒤 지난 시간")
            age.add_metric([], now - ev["finished_at"].timestamp())
            yield age

        if "users" in s:
            users = GaugeMetricFamily("dartrag_users", "가입한 사용자 수")
            users.add_metric([], s["users"])
            yield users
