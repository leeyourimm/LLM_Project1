"""평가 실행과 리포트."""

import json
import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from dartrag.answer import Answer
from dartrag.eval.cases import CATEGORIES, EvalCase
from dartrag.eval.grading import Grade, grade
from dartrag.search import SearchFilter

log = logging.getLogger(__name__)


def run_eval(
    cases: list[EvalCase],
    answer_fn: Callable[[str, SearchFilter], Answer],
    resolve_stocks: Callable[[list[str]], list[str]] = lambda s: [],
    on_progress: Callable[[int, Grade], None] | None = None,
) -> list[Grade]:
    grades = []
    for i, case in enumerate(cases, 1):
        corp_codes = case.corp_codes or (resolve_stocks(case.stocks) if case.stocks else [])
        start = time.perf_counter()
        try:
            answer = answer_fn(case.question, SearchFilter(corp_codes=corp_codes))
        except Exception as e:  # 한 문항 실패로 전체 평가를 멈추지 않는다
            log.exception("문항 실행 실패 %s", case.id)
            g = Grade(case.id, case.category, passed=False, reasons=[f"실행 오류: {e}"])
        else:
            g = grade(case, answer, time.perf_counter() - start)
        grades.append(g)
        if on_progress:
            on_progress(i, g)
    return grades


def _rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(grades: list[Grade]) -> dict:
    def block(gs: list[Grade]) -> dict:
        nums = [g.number_recall for g in gs if g.number_recall is not None]
        hits = [g.retrieval_hit for g in gs if g.retrieval_hit is not None]
        answerable = [g for g in gs if g.category != "unanswerable"]
        lat = [g.latency_s for g in gs if g.latency_s]
        return {
            "count": len(gs),
            "pass_rate": _rate([g.passed for g in gs]),
            "retrieval_hit_rate": _rate(hits),
            "number_recall": statistics.mean(nums) if nums else None,
            "citation_rate": _rate([g.cited for g in answerable]),
            "invalid_citation_rate": _rate([g.invalid_citation for g in answerable]),
            "unverified_number_rate": _rate([g.unverified_numbers > 0 for g in answerable]),
            "latency_p50_s": statistics.median(lat) if lat else None,
        }

    return {
        "overall": block(grades),
        "by_category": {
            c: block([g for g in grades if g.category == c])
            for c in CATEGORIES
            if any(g.category == c for g in grades)
        },
    }


def _fmt(key: str, v) -> str:
    if v is None:
        return "-"
    if key.endswith("_s"):
        return f"{v:.1f}s"
    return f"{v:.1%}" if isinstance(v, float) else str(v)


def write_report(out_dir: Path, grades: list[Grade], meta: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(grades)
    (out_dir / "grades.jsonl").write_text(
        "".join(json.dumps(asdict(g), ensure_ascii=False) + "\n" for g in grades), "utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps({"meta": meta, **summary}, ensure_ascii=False, indent=2), "utf-8"
    )
    keys = list(summary["overall"])
    rows = [("전체", summary["overall"]), *summary["by_category"].items()]
    lines = [
        "# 평가 결과",
        "",
        " · ".join(f"{k}: {v}" for k, v in meta.items()),
        "",
        "| 구분 | " + " | ".join(keys) + " |",
        "|" + " --- |" * (len(keys) + 1),
        *(f"| {name} | " + " | ".join(_fmt(k, b[k]) for k in keys) + " |" for name, b in rows),
        "",
        "## 실패 문항",
        "",
    ]
    for g in grades:
        if not g.passed:
            answer = g.answer.replace("\n", " ")[:200]
            lines.append(f"- `{g.case_id}` ({g.category}): {'; '.join(g.reasons)}\n  > {answer}")
    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path
