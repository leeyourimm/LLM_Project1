"""배포 기준 검사 (release gate).

평가 요약(summary.json)을 eval/release_criteria.toml 의 기준과 직전 기준선에 비교한다.
기준을 하나라도 못 넘으면 배포하지 않는다.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

CRITERIA_PATH = Path("eval/release_criteria.toml")
BASELINE_PATH = Path("eval/baseline.json")


@dataclass(frozen=True)
class Check:
    name: str  # 예: "전체 pass_rate", "numeric number_recall"
    actual: float | None
    required: str  # 예: "≥ 80.0%"
    ok: bool


def _fmt(metric: str, v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.1f}s" if metric.endswith("_s") else f"{v:.1%}"


def load_criteria(path: Path = CRITERIA_PATH) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _bounds(block_name: str, block: dict | None, rules: dict) -> list[Check]:
    out = []
    for key, limit in rules.items():
        kind, _, metric = key.partition("_")
        actual = (block or {}).get(metric)
        if kind == "min":
            ok = actual is not None and actual >= limit
            required = f"≥ {_fmt(metric, limit)}"
        elif kind == "max":
            ok = actual is not None and actual <= limit
            required = f"≤ {_fmt(metric, limit)}"
        else:
            raise ValueError(f"기준 이름은 min_ 또는 max_ 로 시작해야 합니다: {key}")
        out.append(Check(f"{block_name} {metric}", actual, required, ok))
    return out


def check_release(summary: dict, criteria: dict, baseline: dict | None = None) -> list[Check]:
    checks = _bounds("전체", summary.get("overall"), criteria.get("overall", {}))
    by_cat = summary.get("by_category", {})
    for cat, rules in criteria.get("category", {}).items():
        if cat not in by_cat:
            checks.append(Check(f"{cat} 문항", None, "1개 이상", False))
            continue
        checks += _bounds(cat, by_cat[cat], rules)
    reg = criteria.get("regression", {})
    count = (summary.get("overall") or {}).get("count") or 0
    if "min_cases" in reg:
        checks.append(
            Check("문항 수", float(count), f"≥ {reg['min_cases']}", count >= reg["min_cases"])
        )
    if baseline and "max_pass_rate_drop" in reg:
        cur = (summary.get("overall") or {}).get("pass_rate")
        base = (baseline.get("overall") or {}).get("pass_rate")
        if cur is not None and base is not None:
            drop = base - cur
            checks.append(
                Check(
                    "기준선 대비 통과율 하락",
                    drop,
                    f"≤ {reg['max_pass_rate_drop']:.1%} (기준선 {base:.1%})",
                    drop <= reg["max_pass_rate_drop"],
                )
            )
    return checks


def render_checks(checks: list[Check]) -> str:
    lines = ["| 기준 | 결과 | 필요 | 판정 |", "| --- | --- | --- | --- |"]
    for c in checks:
        metric = c.name.rsplit(" ", 1)[-1]
        actual = str(int(c.actual)) if c.name == "문항 수" else _fmt(metric, c.actual)
        lines.append(f"| {c.name} | {actual} | {c.required} | {'통과' if c.ok else '실패'} |")
    failed = sum(not c.ok for c in checks)
    verdict = "배포 가능" if not failed else f"배포 불가 (기준 {failed}개 미달)"
    return "\n".join([*lines, "", f"**{verdict}**"])


def load_baseline(path: Path = BASELINE_PATH) -> dict | None:
    import json

    return json.loads(path.read_text("utf-8")) if path.exists() else None


def ci_check(
    baseline_path: Path = BASELINE_PATH,
    criteria_path: Path = CRITERIA_PATH,
    prompt_version: int | None = None,
) -> tuple[bool, str]:
    """CI 용: 저장소의 기준선이 배포 기준을 넘는지, 현재 프롬프트로 잰 것인지.

    프롬프트·모델을 바꾸는 PR 은 실제 평가를 다시 돌려 eval/baseline.json 을 함께 올려야 한다.
    기준선이 아직 없으면(첫 실데이터 평가 전) 통과로 두고 그 사실을 알린다."""
    baseline = load_baseline(baseline_path)
    if baseline is None:
        return True, f"{baseline_path} 가 아직 없어 배포 기준 검사를 건너뜁니다."
    lines = []
    ok = True
    meta = baseline.get("meta", {})
    if prompt_version is not None and str(meta.get("prompt")) != str(prompt_version):
        ok = False
        lines.append(
            f"기준선은 프롬프트 v{meta.get('prompt')} 로 쟀는데 "
            f"지금 코드는 v{prompt_version} 입니다. "
            "dartrag eval run --gate 로 다시 재고 dartrag eval baseline 으로 갱신하세요."
        )
    checks = check_release(baseline, load_criteria(criteria_path))
    ok = ok and all(c.ok for c in checks)
    lines.append(render_checks(checks))
    return ok, "\n\n".join(lines)
