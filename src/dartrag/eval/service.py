"""평가 실행 → 리포트 → 배포 기준 판정 → 기록. CLI 와 정기 평가 작업이 같이 쓴다."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dartrag.eval.cases import EvalCase
from dartrag.eval.gate import check_release, load_baseline, load_criteria, render_checks
from dartrag.eval.runner import run_eval, summarize, write_report


def run_and_record(
    repo,
    answerer,
    cases: list[EvalCase],
    out: Path,
    meta: dict,
    *,
    on_progress: Callable | None = None,
) -> dict:
    """{"summary", "checks", "passed", "report", "gate"}."""
    grades = run_eval(cases, answerer.answer, repo.corp_codes_for_stocks, on_progress)
    run_dir = out / datetime.now().strftime("%Y%m%d-%H%M%S")
    report = write_report(run_dir, grades, meta)
    summary = summarize(grades)
    checks = check_release(summary, load_criteria(), load_baseline())
    passed = all(c.ok for c in checks)
    gate = render_checks(checks)
    (run_dir / "gate.md").write_text("# 배포 기준\n\n" + gate + "\n", "utf-8")
    repo.save_eval_run(meta, summary, passed)
    return {
        "summary": summary,
        "checks": checks,
        "passed": passed,
        "report": report,
        "summary_path": run_dir / "summary.json",
        "gate": gate,
    }


def promote_reviewed(candidates: Path, target: Path) -> tuple[int, int]:
    """검수한 후보 문항(정답을 채운 것)을 평가셋으로 옮긴다. (옮긴 수, 남은 수).

    정답이 채워졌다고 보는 기준: 숫자·키워드·근거 중 하나라도 있거나, 답이 없어야 하는
    문항(unanswerable)·거절해야 하는 문항(adversarial)으로 분류를 바꾼 것."""
    from dartrag.eval.cases import load_cases, save_cases

    pending = load_cases(candidates) if candidates.exists() else []
    existing = load_cases(target) if target.exists() else []
    known = {c.id for c in existing}

    def reviewed(c: EvalCase) -> bool:
        return bool(
            c.expected_numbers
            or c.expected_keywords
            or c.expected_sources
            or c.category in ("unanswerable", "adversarial")
        )

    moved = [c for c in pending if reviewed(c) and c.id not in known]
    left = [c for c in pending if not reviewed(c)]
    if moved:
        save_cases(target, existing + moved)
    save_cases(candidates, left)
    return len(moved), len(left)
