"""재무 데이터 검증 규칙.

수집한 숫자가 이상하면 답변·대시보드에 그대로 나가기 전에 찾아낸다.
결과는 data_issues 테이블에 남기고, 화면에는 "확인 필요" 표시로 보여 준다.

- balance (오류): 자산총계 = 부채총계 + 자본총계 가 0.5% 넘게 어긋남
- negative (오류): 자산총계가 0 이하이거나 매출액이 음수
- unit (경고): 자산총계가 1억원 미만 (단위를 잘못 읽었을 가능성)
- missing (경고): 사업보고서에 매출액이나 자산총계가 없음
- jump (경고): 같은 기준(연결/별도) 연간 매출액이 전년보다 4배 넘게 늘거나 4분의 1 아래로 줄어듦
"""

from dataclasses import dataclass

from dartrag.finance.accounts import METRICS, STATEMENT_SJ_DIV
from dartrag.finance.calc import fmt_won
from dartrag.finance.tool import pick_values

ANNUAL = "11011"
BALANCE_TOLERANCE = 0.005
JUMP_UP, JUMP_DOWN = 4.0, 0.25
MIN_ASSETS = 10**8
CHECKED = ("revenue", "total_assets", "total_liabilities", "total_equity")


@dataclass(frozen=True)
class Issue:
    corp_code: str
    bsns_year: int
    reprt_code: str
    fs_div: str
    rule: str
    severity: str  # error / warn
    detail: str


def _key(r):
    return (r.bsns_year, r.reprt_code, r.fs_div)


def _values(repo, corp_code: str) -> dict[str, dict[tuple, int]]:
    """지표 → (연도, 보고서, 연결/별도) → 금액."""
    out = {}
    for key in CHECKED:
        m = METRICS[key]
        rows = repo.quarter_rows(
            corp_code,
            STATEMENT_SJ_DIV[m.statement],
            m.account_ids,
            tuple(n.replace(" ", "") for n in m.account_names),
        )
        out[key] = {k: v.amount for k, v in pick_values(rows, m, key=_key).items()}
    return out


def check_values(corp_code: str, values: dict[str, dict[tuple, int]]) -> list[Issue]:
    issues: list[Issue] = []

    def add(k, rule, severity, detail):
        issues.append(Issue(corp_code, k[0], k[1], k[2], rule, severity, detail))

    assets, liab = values["total_assets"], values["total_liabilities"]
    equity = values["total_equity"]
    revenue = values["revenue"]
    periods = sorted(set().union(*values.values()))
    for k in periods:
        a, li, e, r = assets.get(k), liab.get(k), equity.get(k), revenue.get(k)
        if a is not None and li is not None and e is not None and a > 0:
            gap = abs(a - (li + e)) / a
            if gap > BALANCE_TOLERANCE:
                add(
                    k,
                    "balance",
                    "error",
                    f"자산총계 {fmt_won(a, exact=False)} ≠ 부채총계+자본총계 "
                    f"{fmt_won(li + e, exact=False)} (차이 {gap:.1%})",
                )
        if a is not None and a <= 0:
            add(k, "negative", "error", f"자산총계가 {fmt_won(a, exact=False)}")
        elif a is not None and a < MIN_ASSETS:
            add(k, "unit", "warn", f"자산총계가 {a:,}원으로 지나치게 작음 (단위 확인)")
        if r is not None and r < 0:
            add(k, "negative", "error", f"매출액이 {fmt_won(r, exact=False)}")
        if k[1] == ANNUAL:
            missing = [
                METRICS[m].label
                for m, vs in (("revenue", revenue), ("total_assets", assets))
                if k not in vs
            ]
            if missing:
                add(k, "missing", "warn", f"사업보고서에 {', '.join(missing)} 없음")
    for k, cur in revenue.items():
        if k[1] != ANNUAL:
            continue
        prev = revenue.get((k[0] - 1, ANNUAL, k[2]))
        if prev and prev > 0 and cur > 0:
            ratio = cur / prev
            if ratio > JUMP_UP or ratio < JUMP_DOWN:
                add(
                    k,
                    "jump",
                    "warn",
                    f"매출액이 전년 {fmt_won(prev, exact=False)}에서 "
                    f"{fmt_won(cur, exact=False)}로 {ratio:.1f}배",
                )
    return issues


def validate_company(repo, corp_code: str) -> list[Issue]:
    issues = check_values(corp_code, _values(repo, corp_code))
    repo.replace_issues(corp_code, issues)
    return issues


def run_validation(repo, corp_codes: list[str] | None = None) -> dict[str, int]:
    """회사별로 검증하고 결과를 저장한다. {"companies": n, "error": n, "warn": n}."""
    codes = corp_codes or [c for c, _ in repo.listed_companies()]
    counts = {"companies": 0, "error": 0, "warn": 0}
    for code in codes:
        for issue in validate_company(repo, code):
            counts[issue.severity] += 1
        counts["companies"] += 1
    return counts
