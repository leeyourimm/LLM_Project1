from decimal import Decimal

import pytest

from dartrag.answer.numbers import extract, parse_unit, split_sentences, unverified_numbers

TABLE = "| 부문 | 매출액 |\n| --- | --- |\n| DS | 111,066,000 |\n| DX | (174,887,000) |"


@pytest.mark.parametrize(
    "text, value, kind",
    [
        ("1조 2,345억원", Decimal("1234500000000"), "money"),
        ("6.6조원", Decimal("6600000000000"), "money"),
        ("3,000억", Decimal("300000000000"), "money"),
        ("1,234 백만원", Decimal("1234000000"), "money"),
        ("12.5%", Decimal("12.5"), "percent"),
        ("1,234,567원", Decimal("1234567"), "money"),
    ],
)
def test_extract_korean_amounts(text, value, kind):
    [q] = extract(f"금액은 {text} 입니다")
    assert (q.value, q.kind) == (value, kind)


def test_extract_skips_dates_and_citations():
    qs = extract("2024년 12월 31일 기준(2024.12) 매출은 300조원 [1][2]")
    assert [q.text for q in qs] == ["300조원"]


def test_parse_unit():
    assert parse_unit("백만원") == (Decimal(10) ** 6, "money")
    assert parse_unit(" 원 ") == (1, "money")
    assert parse_unit("%") == (1, "percent")
    assert parse_unit("주") is None and parse_unit(None) is None


def test_table_unit_conversion_and_rounding():
    src = [(TABLE, "백만원")]
    assert unverified_numbers("DS 매출은 111조원입니다 [1].", src) == []
    assert unverified_numbers("DS 매출은 111.1조원입니다 [1].", src) == []
    assert unverified_numbers("DS 매출은 111,066,000백만원입니다.", src) == []
    # 자릿수를 틀린 경우
    assert unverified_numbers("DS 매출은 11.1조원입니다 [1].", src) == ["11.1조원"]
    assert unverified_numbers("DS 매출은 112조원입니다 [1].", src) == ["112조원"]
    # 음수(괄호) 표기도 절댓값으로 비교
    assert unverified_numbers("DX 는 174.9조원 적자", src) == []


def test_percent_and_small_integers():
    src = [("영업이익률은 12.5% 이다. 배당은 연 4회 지급한다.", None)]
    assert unverified_numbers("이익률 12.5%, 연 4회 배당 [1]", src) == []
    assert unverified_numbers("이익률 13.5% [1]", src) == ["13.5%"]
    assert unverified_numbers("3분기 2회", []) == []  # 작은 정수는 검사 안 함


def test_split_sentences_keeps_trailing_citations():
    text = "매출이 늘었다. [1] 이익은 줄었다 [2].\n배당은 유지했다."
    assert split_sentences(text) == ["매출이 늘었다. [1] 이익은 줄었다 [2].", "배당은 유지했다."]
