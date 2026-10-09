"""재무 DB 조회 → 계산 → 답변용 출처 한 건.

결과를 검색 결과(SearchHit)와 같은 모양으로 만들어 프롬프트에 [1] 같은 출처로 넣는다.
그러면 인용 표시와 숫자 검증이 공시 원문과 똑같이 동작한다.
"""

from dataclasses import dataclass

from dartrag.finance.accounts import METRICS, RATIOS, STATEMENT_SJ_DIV, Metric
from dartrag.finance.calc import fmt_pct, fmt_won, growth, ratio
from dartrag.finance.query import FinanceQuery, parse_question
from dartrag.search import SearchFilter, SearchHit

FS_LABEL = {"CFS": "연결", "OFS": "별도"}


@dataclass(frozen=True)
class FinancialRow:
    corp_code: str
    bsns_year: int
    fs_div: str
    account_id: str | None
    account_nm: str  # 공백 제거
    amount: int | None
    rcept_no: str
    ord: int | None = None
    reprt_code: str | None = None
    add_amount: int | None = None  # 분기·반기 보고서의 누적 금액


@dataclass(frozen=True)
class Value:
    amount: int
    fs_div: str
    account_nm: str
    rcept_no: str
    add_amount: int | None = None


def _corp_year(r: FinancialRow) -> tuple:
    return (r.corp_code, r.bsns_year)


def pick_values(rows: list[FinancialRow], metric: Metric, key=_corp_year) -> dict[tuple, Value]:
    """회사·연도(key)별로 가장 알맞은 계정 하나: 연결 우선, 표준 계정 ID 우선, 계정명 우선순위."""
    names = [n.replace(" ", "") for n in metric.account_names]

    def rank(r: FinancialRow):
        by_id = r.account_id in metric.account_ids
        by_name = names.index(r.account_nm) if r.account_nm in names else len(names)
        return (r.fs_div != "CFS", not by_id, by_name, r.ord or 0)

    best: dict[tuple, FinancialRow] = {}
    for r in rows:
        if r.amount is None:
            continue
        if r.account_id not in metric.account_ids and r.account_nm not in names:
            continue
        k = key(r)
        if k not in best or rank(r) < rank(best[k]):
            best[k] = r
    return {
        k: Value(r.amount, r.fs_div, r.account_nm, r.rcept_no, r.add_amount)
        for k, r in best.items()
    }


class FinanceTool:
    def __init__(self, repo):
        self.repo = repo
        self._companies: list[tuple[str, str]] | None = None

    def companies(self) -> list[tuple[str, str]]:
        if self._companies is None:
            self._companies = self.repo.listed_companies()
        return self._companies

    def lookup(self, question: str, flt: SearchFilter | None = None) -> SearchHit | None:
        flt = flt or SearchFilter()
        query = parse_question(question, self.companies(), flt.corp_codes)
        if query is None:
            return None
        values = {
            key: pick_values(
                self.repo.financial_rows(
                    query.corp_codes,
                    query.reprt_code,
                    STATEMENT_SJ_DIV[METRICS[key].statement],
                    METRICS[key].account_ids,
                    tuple(n.replace(" ", "") for n in METRICS[key].account_names),
                ),
                METRICS[key],
            )
            for key in query.needed_metrics
        }
        body = render(query, values, dict(self.companies()))
        if body is None:
            return None
        first = next((v for vs in values.values() for v in vs.values()), None)
        names = dict(self.companies())
        return SearchHit(
            "finance",
            1.0,
            None,
            None,
            {
                "chunk_id": "finance",
                "rcept_no": first.rcept_no if first else None,
                # 표에 쓴 값이 나온 보고서 전부 (답변 신뢰도의 최신 공시 판단에 쓴다)
                "rcept_nos": used_rcept_nos(query, values),
                "corp_name": ", ".join(names.get(c, c) for c in query.corp_codes),
                "report_nm": f"OpenDART 재무제표 ({query.period_label})",
                "kind": "finance",
                "section_path": ["재무 데이터"],
                "body": body,
                "unit": None,
                "url": (
                    f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={first.rcept_no}"
                    if first
                    else "https://opendart.fss.or.kr"
                ),
            },
        )


def _years(query: FinanceQuery, values: dict[str, dict[tuple[str, int], Value]], corp: str):
    if query.years:
        return query.years
    available = sorted({y for vs in values.values() for (c, y) in vs if c == corp})
    return available[-2:] if query.growth else available[-1:]


def used_rcept_nos(query: FinanceQuery, values: dict[str, dict[tuple[str, int], Value]]):
    """render 가 표에 넣는 값들의 접수번호 (오래된 것부터)."""
    used = {
        v.rcept_no
        for corp in query.corp_codes
        for key in query.needed_metrics
        for y in _years(query, values, corp)
        if (v := values[key].get((corp, y)))
    }
    return sorted(used)


def render(
    query: FinanceQuery,
    values: dict[str, dict[tuple[str, int], Value]],
    names: dict[str, str],
) -> str | None:
    lines = ["| 회사 | 사업연도 | 재무제표 | 항목 | 금액 |", "| --- | --- | --- | --- | --- |"]
    calcs: list[str] = []
    found = False
    for corp in query.corp_codes:
        name = names.get(corp, corp)
        years = _years(query, values, corp)
        for key in query.needed_metrics:
            label = METRICS[key].label
            for y in years:
                v = values[key].get((corp, y))
                if v is None:
                    lines.append(f"| {name} | {y} | - | {label} | 데이터 없음 |")
                    continue
                found = True
                lines.append(
                    f"| {name} | {y} | {FS_LABEL[v.fs_div]} | {label} | {fmt_won(v.amount)} |"
                )
            if query.growth and key in query.metrics:
                for prev, cur in zip(years, years[1:], strict=False):
                    a, b = values[key].get((corp, cur)), values[key].get((corp, prev))
                    if a and b:
                        cur_s, prev_s = fmt_won(a.amount, False), fmt_won(b.amount, False)
                        pct = fmt_pct(growth(a.amount, b.amount))
                        calcs.append(
                            f"- {name} {label} {prev}→{cur} 증감률: {pct}"
                            f" (= ({cur_s} - {prev_s}) ÷ {prev_s} × 100)"
                        )
        for rkey in query.ratios:
            r = RATIOS[rkey]
            for y in years:
                n, d = values[r.numerator].get((corp, y)), values[r.denominator].get((corp, y))
                if n and d:
                    calcs.append(
                        f"- {name} {y} {r.label}: {fmt_pct(ratio(n.amount, d.amount))}"
                        f" (= {METRICS[r.numerator].label} {fmt_won(n.amount, False)}"
                        f" ÷ {METRICS[r.denominator].label} {fmt_won(d.amount, False)} × 100)"
                    )
    if not found:
        return None
    out = "\n".join(lines)
    if calcs:
        out += "\n\n계산 (코드로 계산한 값):\n" + "\n".join(calcs)
    return out
