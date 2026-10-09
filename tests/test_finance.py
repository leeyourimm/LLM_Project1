from decimal import Decimal

import pytest

from dartrag.answer import Answerer
from dartrag.finance import FinanceTool, FinancialRow, parse_question
from dartrag.finance.accounts import METRICS
from dartrag.finance.calc import fmt_pct, fmt_won, growth, ratio
from dartrag.finance.query import find_companies
from dartrag.finance.tool import pick_values
from dartrag.search import SearchFilter

SAMSUNG, HYNIX = "00126380", "00164779"
COMPANIES = [(SAMSUNG, "삼성전자"), (HYNIX, "에스케이하이닉스"), ("00126371", "삼성전자우")]


@pytest.mark.parametrize(
    "question, metrics, ratios, years, reprt, growth_",
    [
        ("2024년 영업이익률은?", [], ["operating_margin"], [2024], "11011", False),
        ("2022~2024 매출 추이", ["revenue"], [], [2022, 2023, 2024], "11011", True),
        ("24년 3분기 부채비율", [], ["debt_ratio"], [2024], "11014", False),
        ("2024년 매출 증가율", ["revenue"], [], [2023, 2024], "11011", True),
        ("상반기 당기순이익과 자산총계", ["net_income", "total_assets"], [], [], "11012", False),
    ],
)
def test_parse_question(question, metrics, ratios, years, reprt, growth_):
    q = parse_question(question, COMPANIES, [SAMSUNG])
    assert (q.metrics, q.ratios, q.years, q.reprt_code, q.growth) == (
        metrics,
        ratios,
        years,
        reprt,
        growth_,
    )


def test_parse_question_non_financial_or_no_company():
    assert parse_question("HBM 사업 전략은?", COMPANIES, [SAMSUNG]) is None
    assert parse_question("영업이익은?", COMPANIES, []) is None


def test_find_companies_handles_aliases_and_overlaps():
    assert find_companies("SK하이닉스와 삼성전자 비교", COMPANIES) == [HYNIX, SAMSUNG]
    assert find_companies("삼성전자우 배당", COMPANIES) == ["00126371"]


def test_ratio_needs_components():
    q = parse_question("2024년 영업이익률", COMPANIES, [SAMSUNG])
    assert q.needed_metrics == ["operating_income", "revenue"]


def test_calc():
    assert growth(120, 100) == Decimal(20)
    assert growth(-50, -100) == Decimal(50)  # 적자 축소
    assert growth(10, -5) is None and growth(5, 0) is None  # 흑자 전환, 0 기준
    assert ratio(1, 3).quantize(Decimal("0.01")) == Decimal("33.33")
    assert ratio(1, 0) is None
    assert fmt_pct(Decimal("16.249")) == "16.2%" and fmt_pct(Decimal("0.05")) == "0.1%"
    assert fmt_won(302_231_360_000_000) == "302조 2,314억원 (302,231,360,000,000원)"
    assert fmt_won(-123_456_789_012, exact=False) == "-1,235억원"
    assert fmt_won(9_999_999_950_000_000, exact=False) == "10,000조원"
    assert fmt_won(9_999) == "9,999원"


def row(year, amount, fs="CFS", aid="ifrs-full_Revenue", nm="매출액", corp=SAMSUNG):
    return FinancialRow(corp, year, fs, aid, nm, amount, f"2025031100000{year % 10}")


def test_pick_values_prefers_consolidated_and_standard_id():
    rows = [
        row(2024, 1, fs="OFS"),
        row(2024, 2, aid="-표준계정코드 미사용-", nm="영업수익"),
        row(2024, 3),
        row(2023, 4, aid="-표준계정코드 미사용-", nm="수익(매출액)"),
        row(2023, None),
        row(2022, 5, aid="x", nm="기타수익"),  # 매핑에 없는 계정
    ]
    got = pick_values(rows, METRICS["revenue"])
    assert {k: v.amount for k, v in got.items()} == {(SAMSUNG, 2024): 3, (SAMSUNG, 2023): 4}


class FakeRepo:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def listed_companies(self):
        return COMPANIES

    def quarter_rows(self, corp_code, sj_divs, account_ids, account_names):
        return [r for r in self.rows if r.corp_code == corp_code and r.account_id in account_ids]

    def financial_rows(self, corp_codes, reprt_code, sj_divs, account_ids, account_names):
        self.calls.append((tuple(corp_codes), reprt_code, sj_divs))
        return [r for r in self.rows if r.corp_code in corp_codes and (r.account_id in account_ids)]


ROWS = [
    row(2023, 258_935_494_000_000),
    row(2024, 300_870_903_000_000),
    row(2023, 6_566_976_000_000, aid="dart_OperatingIncomeLoss", nm="영업이익"),
    row(2024, 32_725_961_000_000, aid="dart_OperatingIncomeLoss", nm="영업이익"),
]


def test_finance_tool_renders_table_and_calculations():
    tool = FinanceTool(FakeRepo(ROWS))
    hit = tool.lookup("삼성전자 2024년 매출 증가율과 영업이익률")
    body = hit.chunk["body"]
    assert "| 삼성전자 | 2024 | 연결 | 매출액 | 300조 8,709억원 (300,870,903,000,000원) |" in body
    assert "매출액 2023→2024 증감률: 16.2%" in body
    assert "2024 영업이익률: 10.9%" in body
    assert "2023 영업이익률: 2.5%" in body
    assert hit.chunk["section_path"] == ["재무 데이터"]
    assert hit.chunk["url"].startswith("https://dart.fss.or.kr/")


def test_finance_tool_latest_year_and_missing_data():
    tool = FinanceTool(FakeRepo(ROWS))
    body = tool.lookup("매출액은?", SearchFilter(corp_codes=[SAMSUNG])).chunk["body"]
    assert "| 2024 |" in body and "| 2023 |" not in body
    body = tool.lookup("2022~2024 매출액", SearchFilter(corp_codes=[SAMSUNG])).chunk["body"]
    assert "| 삼성전자 | 2022 | - | 매출액 | 데이터 없음 |" in body
    assert tool.lookup("SK하이닉스 매출액") is None  # 데이터가 하나도 없으면 출처로 안 넣음
    assert tool.lookup("HBM 전략") is None


class FakeRetriever:
    def __init__(self):
        self.limits = []

    def search(self, query, flt=None, limit=10):
        self.limits.append(limit)
        return []


class FakeLLM:
    name = "fake"

    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return self.reply


def test_answerer_puts_finance_first_and_verifies_its_numbers():
    retriever = FakeRetriever()
    llm = FakeLLM("2024년 매출은 300.9조원으로 전년보다 16.2% 늘었습니다 [1].")
    answerer = Answerer(retriever, llm, finance=FinanceTool(FakeRepo(ROWS)), top_k=8)
    result = answerer.answer("삼성전자 2024년 매출 증가율")
    assert retriever.limits == [7]
    assert "[1] 삼성전자 | OpenDART 재무제표 (연간) | 재무 데이터" in llm.messages[1].content
    assert result.citations[0].hit.chunk_id == "finance"
    assert result.warnings == []

    wrong = Answerer(
        FakeRetriever(),
        FakeLLM("매출은 16.5% 늘었습니다 [1]."),
        finance=FinanceTool(FakeRepo(ROWS)),
    ).answer("삼성전자 2024년 매출 증가율")
    assert wrong.unverified == ["16.5%"]


def test_company_series():
    from dartrag.finance.series import company_series

    rows = ROWS + [
        row(2022, 302_231_360_000_000),
        row(2024, 112_339_878_000_000, aid="ifrs-full_Liabilities", nm="부채총계"),
        row(2024, 402_192_070_000_000, aid="ifrs-full_Equity", nm="자본총계"),
        row(2020, 236_806_988_000_000),  # 2021 이 빠져 2022 증감률은 계산하지 않음
    ]
    series = company_series(FakeRepo(rows), SAMSUNG, years=4)
    assert [p.year for p in series] == [2020, 2022, 2023, 2024]
    last = series[-1]
    assert last.values["revenue"] == 300_870_903_000_000 and last.fs_div == "연결"
    assert last.ratios["operating_margin"] == 10.88
    assert last.ratios["debt_ratio"] == 27.93
    assert last.growth["revenue"] == 16.2
    assert series[-2].ratios["debt_ratio"] is None  # 부채·자본이 없으면 비율 없음
    assert series[1].growth == {}  # 2021 이 없음
    assert series[2].growth["operating_income"] is None  # 2022 영업이익 없음
    assert company_series(FakeRepo([]), SAMSUNG) == []


def qrow(year, reprt, amount, add=None, aid="ifrs-full_Revenue"):
    return FinancialRow(SAMSUNG, year, "CFS", aid, "x", amount, "r", None, reprt, add)


def test_quarterly_series_derives_q4():
    from dartrag.finance.series import quarterly_series

    rows = [
        qrow(2024, "11013", 71),
        qrow(2024, "11012", 74),
        qrow(2024, "11014", 79, add=224),
        qrow(2024, "11011", 300),
        # 2023: 3분기 누적이 없으면 1~3분기 합으로 계산
        qrow(2023, "11013", 63),
        qrow(2023, "11012", 60),
        qrow(2023, "11014", 67),
        qrow(2023, "11011", 259),
        qrow(2022, "11011", 302),  # 분기 자료가 없으면 4분기도 못 구함
        qrow(2024, "11013", 6, aid="dart_OperatingIncomeLoss"),
    ]
    pts = quarterly_series(FakeRepo(rows), SAMSUNG, quarters=8)
    assert [p.label for p in pts] == [f"{y} {q}Q" for y in (2023, 2024) for q in (1, 2, 3, 4)]
    q4_24 = pts[-1]
    assert q4_24.values["revenue"] == 76 and q4_24.derived == ["revenue"]
    assert pts[3].values["revenue"] == 259 - (63 + 60 + 67)
    assert pts[4].values["operating_income"] == 6 and pts[5].values["operating_income"] is None


def test_compare_table_uses_common_year():
    from dartrag.finance.series import YearPoint, compare_table

    a = [YearPoint(2023), YearPoint(2024)]
    b = [YearPoint(2022), YearPoint(2023)]
    t = compare_table({"A": a, "B": b, "C": []})
    assert t["year"] == 2023 and t["points"]["A"].year == 2023 and t["points"]["C"] is None
    t = compare_table({"A": [YearPoint(2024)], "B": [YearPoint(2020)]})
    assert t["year"] is None and t["points"]["B"].year == 2020
