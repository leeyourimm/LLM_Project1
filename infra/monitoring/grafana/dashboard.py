"""Grafana 대시보드 JSON 생성기.

고친 뒤 python infra/monitoring/grafana/dashboard.py 로 dashboards/dartrag.json 을 다시 만든다.
"""

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
panels: list[dict] = []
_id = 0
_y = 0


def row(title: str) -> None:
    global _id, _y
    _id += 1
    panels.append(
        {
            "type": "row",
            "id": _id,
            "title": title,
            "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y},
            "panels": [],
        }
    )
    _y += 1


def panel(kind, title, targets, x, w, h=8, unit=None, desc=None, stack=False, **extra):
    global _id
    _id += 1
    defaults = {"unit": unit} if unit else {}
    p = {
        "type": kind,
        "id": _id,
        "title": title,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": _y},
        "targets": [
            {"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": legend}
            for i, (e, legend) in enumerate(targets)
        ],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {},
    }
    if desc:
        p["description"] = desc
    if kind == "timeseries":
        p["fieldConfig"]["defaults"]["custom"] = {
            "lineWidth": 2,
            "fillOpacity": 10 if stack else 0,
            "stacking": {"mode": "normal" if stack else "none"},
            "showPoints": "never",
        }
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom"}}
    p.update(extra)
    panels.append(p)
    return p


def next_row(h=8):
    global _y
    _y += h


def thresholds(*steps):
    return {
        "mode": "absolute",
        "steps": [{"color": c, "value": v} for v, c in steps],
    }


# --- 사용자 경험 --------------------------------------------------------------
row("사용자 경험")
panel(
    "timeseries",
    "API 요청 (초당, 라우트별)",
    [("sum by (route) (rate(dartrag_http_requests_total[5m]))", "{{route}}")],
    0,
    12,
    unit="reqps",
)
panel(
    "timeseries",
    "응답 시간 p95 (라우트별)",
    [
        (
            "histogram_quantile(0.95, sum by (le, route) "
            "(rate(dartrag_http_request_seconds_bucket[5m])))",
            "{{route}}",
        )
    ],
    12,
    12,
    unit="s",
    desc="스트리밍 답변은 응답이 시작될 때까지의 시간",
)
next_row()
p = panel(
    "stat",
    "서버 오류 비율 (1시간)",
    [
        (
            '(sum(increase(dartrag_http_requests_total{status=~"5.."}[1h])) or vector(0)) / '
            "clamp_min(sum(increase(dartrag_http_requests_total[1h])), 1)",
            "",
        )
    ],
    0,
    6,
    h=5,
    unit="percentunit",
)
p["fieldConfig"]["defaults"]["thresholds"] = thresholds(
    (None, "green"), (0.01, "orange"), (0.05, "red")
)
p = panel(
    "stat",
    "👍 비율 (7일)",
    [
        (
            '(sum(increase(dartrag_feedback_total{rating="up"}[7d])) or vector(0)) / '
            "clamp_min(sum(increase(dartrag_feedback_total[7d])), 1)",
            "",
        )
    ],
    6,
    6,
    h=5,
    unit="percentunit",
)
p["fieldConfig"]["defaults"]["thresholds"] = thresholds(
    (None, "red"), (0.6, "orange"), (0.8, "green")
)
panel(
    "timeseries",
    "요청 한도에 걸린 수 (시간당)",
    [("sum by (scope) (increase(dartrag_rate_limited_total[1h]))", "{{scope}}")],
    12,
    6,
    h=5,
)
panel("stat", "가입 사용자", [("dartrag_users", "")], 18, 6, h=5)
next_row(5)

# --- 답변 품질과 속도 ---------------------------------------------------------
row("답변")
panel(
    "timeseries",
    "답변 결과 (시간당)",
    [("sum by (outcome) (increase(dartrag_answers_total[1h]))", "{{outcome}}")],
    0,
    12,
    stack=True,
    desc="answered: 답함, not_found: 공시에서 못 찾음, refused: 투자 권유 등 거절, "
    "cached: 저장된 답, error: 실패",
)
panel(
    "timeseries",
    "답변 단계별 시간 p50 / p95",
    [
        (
            "histogram_quantile(0.5, sum by (le, stage) "
            "(rate(dartrag_answer_stage_seconds_bucket[30m])))",
            "{{stage}} p50",
        ),
        (
            "histogram_quantile(0.95, sum by (le, stage) "
            "(rate(dartrag_answer_stage_seconds_bucket[30m])))",
            "{{stage}} p95",
        ),
    ],
    12,
    12,
    unit="s",
)
next_row()
panel(
    "timeseries",
    "검증 경고가 붙은 답변 비율",
    [
        (
            "sum by (kind) (increase(dartrag_answer_warnings_total[1h])) / "
            "ignoring(kind) group_left clamp_min(sum(increase("
            'dartrag_answers_total{outcome="answered"}[1h])), 1)',
            "{{kind}}",
        )
    ],
    0,
    12,
    unit="percentunit",
    desc="citation: 출처 번호가 없거나 틀림, number: 근거에서 확인되지 않은 숫자",
)
panel(
    "timeseries",
    "LLM 토큰 (시간당)",
    [("sum by (kind) (increase(dartrag_llm_tokens_total[1h]))", "{{kind}}")],
    12,
    12,
    stack=True,
)
next_row()
panel(
    "timeseries",
    "LLM 요청: 답한 모델 (시간당)",
    [
        (
            "sum by (model, outcome) (increase(dartrag_llm_requests_total[1h]))",
            "{{model}} {{outcome}}",
        )
    ],
    0,
    12,
    stack=True,
    desc="ok: 기본 모델이 답함, fallback: 기본 모델이 실패하거나 첫 글자가 늦어 대체 모델이 답함, "
    "error: 모두 실패",
)
panel(
    "timeseries",
    "LLM 실패한 시도 (시간당)",
    [("sum by (model, kind) (increase(dartrag_llm_failures_total[1h]))", "{{model}} {{kind}}")],
    12,
    12,
    stack=True,
    desc="connect·timeout·server·disconnect 는 다시 보냄, missing: 모델 없음(ollama pull), "
    "deadline: 게이트웨이 시간 제한",
)
next_row()

# --- 평가 ---------------------------------------------------------------------
row("정기 평가")
p = panel(
    "stat",
    "배포 기준",
    [("dartrag_eval_release_passed", "")],
    0,
    6,
    h=5,
    desc="가장 최근 평가가 eval/release_criteria.toml 을 모두 넘었는지",
)
p["fieldConfig"]["defaults"]["mappings"] = [
    {
        "type": "value",
        "options": {
            "1": {"text": "통과", "color": "green"},
            "0": {"text": "미달", "color": "red"},
        },
    }
]
panel(
    "stat",
    "최근 평가 결과",
    [("dartrag_eval_metric", "{{metric}}")],
    6,
    12,
    h=5,
    unit="percentunit",
)
panel("stat", "평가 후 지난 시간", [("dartrag_eval_age_seconds", "")], 18, 6, h=5, unit="s")
next_row(5)

# --- 데이터 파이프라인 --------------------------------------------------------
row("데이터 수집과 작업자")
panel(
    "bargauge",
    "작업별 마지막 성공 후 지난 시간",
    [("dartrag_job_last_success_age_seconds", "{{job}}")],
    0,
    12,
    unit="s",
    options={"orientation": "horizontal", "displayMode": "basic"},
)
panel("stat", "실패한 작업", [("sum(dartrag_job_last_failed)", "")], 12, 4, h=4)
panel("stat", "처리 대기 보고서", [("dartrag_ingest_backlog", "")], 16, 4, h=4)
panel(
    "stat",
    "데이터 검증",
    [("dartrag_data_issues", "{{severity}}")],
    20,
    4,
    h=4,
)
p = panel(
    "gauge",
    "오늘 OpenDART 호출",
    [("dartrag_dart_calls_today / dartrag_dart_daily_limit", "")],
    12,
    12,
    h=4,
    unit="percentunit",
    gridPos={"h": 4, "w": 12, "x": 12, "y": _y + 4},
)
p["fieldConfig"]["defaults"]["thresholds"] = thresholds(
    (None, "green"), (0.7, "orange"), (0.9, "red")
)
p["fieldConfig"]["defaults"]["min"] = 0
p["fieldConfig"]["defaults"]["max"] = 1

dashboard = {
    "uid": "dartrag",
    "title": "DART RAG 서비스",
    "tags": ["dartrag"],
    "timezone": "Asia/Seoul",
    "schemaVersion": 39,
    "version": 1,
    "refresh": "1m",
    "time": {"from": "now-24h", "to": "now"},
    "panels": panels,
}
out = Path(__file__).parent / "dashboards" / "dartrag.json"
out.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", "utf-8")
print(f"{len(panels)} panels → {out}")
