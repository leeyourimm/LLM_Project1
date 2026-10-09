"""재무 계산기. LLM 대신 코드가 계산해서 숫자 오류를 막는다."""

from decimal import ROUND_HALF_UP, Decimal


def growth(current: int, previous: int) -> Decimal | None:
    """전년 대비 증감률(%). 이전 값이 0 이거나 음수→양수 전환이면 의미가 없어 None."""
    if previous == 0 or (previous < 0) != (current < 0):
        return None
    return (Decimal(current) - Decimal(previous)) / abs(Decimal(previous)) * 100


def ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator) * 100


def fmt_pct(value: Decimal | None, digits: int = 1) -> str:
    if value is None:
        return "계산 불가"
    q = value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{q}%"


def fmt_won(amount: int, exact: bool = True) -> str:
    """원 단위 금액 → '302조 2,314억원 (302,231,360,000,000원)'."""
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    # 억 단위로 반올림해서 짧게 표기하고, 정확한 값은 괄호에 함께 적는다
    jo, eok = divmod((a + 5 * 10**7) // 10**8, 10**4)
    if jo:
        short = f"{jo:,}조" + (f" {eok:,}억원" if eok else "원")
    elif eok:
        short = f"{eok:,}억원"
    else:
        return f"{sign}{a:,}원"
    return f"{sign}{short} ({sign}{a:,}원)" if exact else f"{sign}{short}"
