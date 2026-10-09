"""회사 한 곳의 연도별 재무 추이 (대시보드용).

답변용 조회(tool.py)와 같은 계정 선택 규칙(pick_values)을 써서
화면에 보이는 숫자와 질문 답변의 숫자가 어긋나지 않게 한다.
"""

from dataclasses import dataclass, field

from dartrag.finance.accounts import METRICS, RATIOS, STATEMENT_SJ_DIV
from dartrag.finance.calc import growth, ratio
from dartrag.finance.query import ANNUAL
from dartrag.finance.tool import FS_LABEL, pick_values

SERIES_METRICS = ("revenue", "operating_income", "net_income", "total_liabilities", "total_equity")
SERIES_RATIOS = ("operating_margin", "net_margin", "debt_ratio")
GROWTH_METRICS = ("revenue", "operating_income", "net_income")


@dataclass
class YearPoint:
    year: int
    values: dict[str, int | None] = field(default_factory=dict)
    ratios: dict[str, float | None] = field(default_factory=dict)
    growth: dict[str, float | None] = field(default_factory=dict)
    fs_div: str | None = None  # 매출액 기준 연결/별도
    rcept_no: str | None = None


def _num(d) -> float | None:
    return None if d is None else round(float(d), 2)


def company_series(repo, corp_code: str, years: int = 5) -> list[YearPoint]:
    """최근 사업보고서 기준 연도별 주요 지표, 오래된 해부터."""
    picked = {
        key: pick_values(
            repo.financial_rows(
                [corp_code],
                ANNUAL[0],
                STATEMENT_SJ_DIV[METRICS[key].statement],
                METRICS[key].account_ids,
                tuple(n.replace(" ", "") for n in METRICS[key].account_names),
            ),
            METRICS[key],
        )
        for key in SERIES_METRICS
    }
    all_years = sorted({y for vs in picked.values() for (_, y) in vs})[-years:]
    points: list[YearPoint] = []
    for y in all_years:
        p = YearPoint(y)
        for key in SERIES_METRICS:
            v = picked[key].get((corp_code, y))
            p.values[key] = v.amount if v else None
        anchor = picked["revenue"].get((corp_code, y)) or next(
            (vs[(corp_code, y)] for vs in picked.values() if (corp_code, y) in vs), None
        )
        if anchor:
            p.fs_div, p.rcept_no = FS_LABEL[anchor.fs_div], anchor.rcept_no
        for rkey in SERIES_RATIOS:
            r = RATIOS[rkey]
            n, d = p.values[r.numerator], p.values[r.denominator]
            p.ratios[rkey] = _num(ratio(n, d)) if n is not None and d is not None else None
        points.append(p)
    for prev, cur in zip(points, points[1:], strict=False):
        if cur.year - prev.year != 1:
            continue
        for key in GROWTH_METRICS:
            a, b = cur.values[key], prev.values[key]
            cur.growth[key] = _num(growth(a, b)) if a is not None and b is not None else None
    return points


# --- 분기 추이 ---------------------------------------------------------------
# OpenDART 분기·반기 보고서의 손익 '당기금액'은 그 분기 3개월 값이고,
# '누적금액'은 연초부터의 합이다.
# 4분기는 따로 보고되지 않으므로 사업보고서(연간) − 3분기 누적으로 계산한다.

QUARTER_REPORTS = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
QUARTER_METRICS = ("revenue", "operating_income", "net_income")


@dataclass
class QuarterPoint:
    year: int
    quarter: int
    values: dict[str, int | None] = field(default_factory=dict)
    derived: list[str] = field(default_factory=list)  # 4분기처럼 계산으로 얻은 지표

    @property
    def label(self) -> str:
        return f"{self.year} {self.quarter}Q"


def quarterly_series(repo, corp_code: str, quarters: int = 12) -> list[QuarterPoint]:
    picked = {}
    for key in QUARTER_METRICS:
        m = METRICS[key]
        rows = repo.quarter_rows(
            corp_code,
            STATEMENT_SJ_DIV[m.statement],
            m.account_ids,
            tuple(n.replace(" ", "") for n in m.account_names),
        )
        picked[key] = pick_values(rows, m, key=lambda r: (r.bsns_year, r.reprt_code))
    years = sorted({y for vs in picked.values() for (y, _) in vs})
    points: list[QuarterPoint] = []
    for y in years:
        for q in (1, 2, 3, 4):
            p = QuarterPoint(y, q)
            for key in QUARTER_METRICS:
                vals = picked[key]
                if q < 4:
                    code = next(c for c, n in QUARTER_REPORTS.items() if n == q)
                    v = vals.get((y, code))
                    p.values[key] = v.amount if v else None
                    continue
                annual, q3 = vals.get((y, "11011")), vals.get((y, "11014"))
                cum3 = q3.add_amount if q3 and q3.add_amount is not None else None
                if cum3 is None:
                    parts = [vals.get((y, c)) for c in ("11013", "11012", "11014")]
                    if all(parts):
                        cum3 = sum(v.amount for v in parts)
                if annual and cum3 is not None:
                    p.values[key] = annual.amount - cum3
                    p.derived.append(key)
                else:
                    p.values[key] = None
            if any(v is not None for v in p.values.values()):
                points.append(p)
    return points[-quarters:]


# --- 기업 비교 ---------------------------------------------------------------


def compare_table(series_by_corp: dict[str, list[YearPoint]]) -> dict:
    """여러 회사의 공통 최신 연도 지표. 공통 연도가 없으면 각자 최신 연도."""
    year_sets = [{p.year for p in s} for s in series_by_corp.values() if s]
    common = set.intersection(*year_sets) if year_sets else set()
    year = max(common) if common else None
    rows = {}
    for corp, series in series_by_corp.items():
        if not series:
            rows[corp] = None
            continue
        point = next((p for p in series if p.year == year), None) if year else series[-1]
        rows[corp] = point
    return {"year": year, "points": rows}
