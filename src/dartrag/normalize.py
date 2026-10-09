"""금액 문자열 정규화."""

from decimal import Decimal, InvalidOperation

UNIT_MULTIPLIERS = {
    "원": 1,
    "천원": 1_000,
    "백만원": 1_000_000,
    "십억원": 1_000_000_000,
    "억원": 100_000_000,
}


def parse_amount(raw: str | None, unit: str = "원") -> int | None:
    """'1,234,567' / '(1,234)' / '-1,234' / '' / '-' → 원 단위 정수.

    값이 없으면 None. 알 수 없는 단위는 ValueError (조용히 틀린 값을 저장하지 않기 위해).
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", "").replace(" ", "")
    if text in ("", "-", "--"):
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.startswith("△"):  # 국내 재무제표의 음수 표기
        negative, text = True, text[1:]
    try:
        value = Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"금액을 해석할 수 없습니다: {raw!r}") from e
    if unit not in UNIT_MULTIPLIERS:
        raise ValueError(f"알 수 없는 단위: {unit!r}")
    value *= UNIT_MULTIPLIERS[unit]
    if value != value.to_integral_value():
        raise ValueError(f"원 단위로 떨어지지 않는 금액: {raw!r} {unit}")
    result = int(value)
    return -result if negative else result
