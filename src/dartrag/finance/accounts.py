"""질문에 자주 나오는 재무 지표와 DART 계정 매핑.

OpenDART 단일회사 전체 재무제표는 IFRS 표준 계정 ID(account_id)를 주지만,
회사 자체 계정은 '-표준계정코드 미사용-' 으로 오므로 계정명으로도 찾는다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    statement: str  # flow: 손익(IS/CIS), stock: 재무상태표(BS), cash: 현금흐름표(CF)
    account_ids: tuple[str, ...]
    account_names: tuple[str, ...]  # 공백 제거 후 비교
    keywords: tuple[str, ...]  # 질문에서 찾는 말 (긴 것부터 매칭)


METRICS: dict[str, Metric] = {
    m.key: m
    for m in [
        Metric(
            "revenue",
            "매출액",
            "flow",
            ("ifrs-full_Revenue",),
            ("매출액", "수익(매출액)", "영업수익", "매출"),
            ("매출액", "매출", "영업수익"),
        ),
        Metric(
            "operating_income",
            "영업이익",
            "flow",
            ("dart_OperatingIncomeLoss",),
            ("영업이익", "영업이익(손실)", "영업손실"),
            ("영업이익", "영업손실", "영업손익"),
        ),
        Metric(
            "net_income",
            "당기순이익",
            "flow",
            ("ifrs-full_ProfitLoss",),
            ("당기순이익", "당기순이익(손실)", "당기순손실", "연결당기순이익"),
            ("당기순이익", "순이익", "당기순손실", "순손실"),
        ),
        Metric(
            "gross_profit",
            "매출총이익",
            "flow",
            ("ifrs-full_GrossProfit",),
            ("매출총이익", "매출총이익(손실)"),
            ("매출총이익",),
        ),
        Metric(
            "total_assets",
            "자산총계",
            "stock",
            ("ifrs-full_Assets",),
            ("자산총계",),
            ("자산총계", "총자산", "자산"),
        ),
        Metric(
            "total_liabilities",
            "부채총계",
            "stock",
            ("ifrs-full_Liabilities",),
            ("부채총계",),
            ("부채총계", "총부채", "부채"),
        ),
        Metric(
            "total_equity",
            "자본총계",
            "stock",
            ("ifrs-full_Equity",),
            ("자본총계",),
            ("자본총계", "총자본", "자기자본"),
        ),
        Metric(
            "cash",
            "현금및현금성자산",
            "stock",
            ("ifrs-full_CashAndCashEquivalents",),
            ("현금및현금성자산",),
            ("현금및현금성자산", "현금성자산"),
        ),
    ]
}

STATEMENT_SJ_DIV = {"flow": ("IS", "CIS"), "stock": ("BS",), "cash": ("CF",)}


@dataclass(frozen=True)
class Ratio:
    key: str
    label: str
    numerator: str
    denominator: str
    keywords: tuple[str, ...]


RATIOS: dict[str, Ratio] = {
    r.key: r
    for r in [
        Ratio("operating_margin", "영업이익률", "operating_income", "revenue", ("영업이익률",)),
        Ratio("net_margin", "순이익률", "net_income", "revenue", ("순이익률", "당기순이익률")),
        Ratio("gross_margin", "매출총이익률", "gross_profit", "revenue", ("매출총이익률",)),
        Ratio("debt_ratio", "부채비율", "total_liabilities", "total_equity", ("부채비율",)),
        Ratio(
            "roe", "ROE(기말 자본 기준)", "net_income", "total_equity", ("roe", "자기자본이익률")
        ),
    ]
}

# 이 말이 있으면 연도별 증감률을 계산한다
GROWTH_KEYWORDS = (
    "증가율",
    "성장률",
    "증감률",
    "감소율",
    "증가",
    "감소",
    "늘었",
    "줄었",
    "변화",
    "추이",
)
