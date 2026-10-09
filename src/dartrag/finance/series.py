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
