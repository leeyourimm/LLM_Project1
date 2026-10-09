import pytest

from dartrag.normalize import parse_amount


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        ("1,234,567", "원", 1_234_567),
        ("-1,234", "원", -1_234),
        ("(1,234)", "원", -1_234),
        ("△500", "원", -500),
        ("12", "백만원", 12_000_000),
        ("1.5", "천원", 1_500),
        ("", "원", None),
        ("-", "원", None),
        (None, "원", None),
    ],
)
def test_parse_amount(raw, unit, expected):
    assert parse_amount(raw, unit) == expected


def test_rejects_unknown_unit():
    with pytest.raises(ValueError):
        parse_amount("1", "달러")


def test_rejects_garbage():
    with pytest.raises(ValueError):
        parse_amount("N/A")


def test_rejects_fractional_won():
    with pytest.raises(ValueError):
        parse_amount("1.5", "원")
