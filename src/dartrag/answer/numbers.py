"""답변 속 숫자를 인용한 공시 원문과 대조한다.

로컬 LLM 은 숫자를 옮기다 자릿수나 단위를 틀리는 일이 잦다. 답변 문장마다
금액·비율을 뽑아, 그 문장이 인용한 출처 원문에 같은 값이 있는지 확인한다.

- 한국어 단위(조·억·만)와 표 단위(백만원, 천원 등)를 원 단위로 환산해 비교한다.
- 답에 적힌 자릿수만큼 반올림 오차를 허용한다 (원문 111,066,000 백만원 → "111조원" 은 일치).
- 연도·날짜·인용 번호·작은 정수(횟수, 순위 등)는 검사하지 않는다.
- 원문에 없는 값은 계산값일 수도 있으므로 오류가 아니라 "확인되지 않음"으로 표시한다.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

_MULTIPLIERS = {
    "조": Decimal(10) ** 12,
    "천억": Decimal(10) ** 11,
    "백억": Decimal(10) ** 10,
    "십억": Decimal(10) ** 9,
    "억": Decimal(10) ** 8,
    "천만": Decimal(10) ** 7,
    "백만": Decimal(10) ** 6,
    "십만": Decimal(10) ** 5,
    "만": Decimal(10) ** 4,
    "천": Decimal(10) ** 3,
}
_MULT_ALT = "|".join(sorted(_MULTIPLIERS, key=len, reverse=True))

_NUM_RE = re.compile(
    rf"(?<![\d.,])(?P<int>\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.(?P<frac>\d+))?"
    rf"\s*(?P<mult>{_MULT_ALT})?\s*(?P<unit>원|%p|%|퍼센트|달러|배)?"
)
_CITATION_RE = re.compile(r"\[\d{1,2}\]")
_DATE_RE = re.compile(
    r"(?:19|20)\d{2}\s*(?:년|[./-])\s*(?:\d{1,2}\s*(?:월|[./-])?\s*(?:\d{1,2}\s*일?)?)?"
    r"|(?:19|20)\d{2}(?=\s*(?:년|회계연도|사업연도|FY))"
)
_SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+(?!\[)|\n+")


@dataclass(frozen=True)
class Quantity:
    text: str
    value: Decimal  # 원 단위 금액이면 원, 비율이면 %, 그 외는 적힌 값
    tol: Decimal  # 적힌 자릿수에 따른 반올림 허용 오차
    kind: str  # money / percent / plain
    # 원문 안 위치 (화면에서 답변이 옮긴 숫자를 표시할 때 쓴다). 비교에는 쓰지 않는다
    start: int = field(default=-1, compare=False)
    end: int = field(default=-1, compare=False)


def _parse(match: re.Match, table_unit: tuple[Decimal, str] | None = None) -> Quantity:
    raw = Decimal(match["int"].replace(",", "") + (f".{match['frac']}" if match["frac"] else ""))
    step = Decimal(1).scaleb(-len(match["frac"] or ""))
    mult = _MULTIPLIERS.get(match["mult"] or "", Decimal(1))
    unit = match["unit"]
    text = match.group(0).strip()
    pos = {"start": match.start(), "end": match.start() + len(match.group(0).rstrip())}
    if unit in ("%", "퍼센트", "%p"):
        return Quantity(text, raw, step / 2, "percent", **pos)
    if unit == "원" or match["mult"]:
        return Quantity(text, raw * mult, step * mult / 2, "money", **pos)
    if table_unit and unit is None:
        t_mult, t_kind = table_unit
        return Quantity(text, raw * t_mult, step * t_mult / 2, t_kind, **pos)
    return Quantity(text, raw, step / 2, "plain", **pos)


def _blank(m: re.Match) -> str:
    # 같은 길이의 공백으로 지워야 남은 숫자의 위치가 원문과 같다
    return " " * len(m.group(0))


def extract(text: str, table_unit: str | None = None) -> list[Quantity]:
    """텍스트에서 수량을 뽑는다. '1조 2,345억원' 같은 복합 표기는 하나로 합친다."""
    text = _DATE_RE.sub(_blank, _CITATION_RE.sub(_blank, text))
    unit = parse_unit(table_unit)
    out: list[Quantity] = []
    prev_end, prev_mult = -1, None
    for m in _NUM_RE.finditer(text):
        q = _parse(m, unit)
        mult = _MULTIPLIERS.get(m["mult"] or "")
        merge = (
            out
            and prev_mult is not None
            and text[prev_end : m.start()].strip() == ""
            and q.kind == "money"
            and (mult is None or mult < prev_mult)
        )
        if merge:
            last = out.pop()
            q = Quantity(
                f"{last.text} {q.text}", last.value + q.value, q.tol, "money", last.start, q.end
            )
        out.append(q)
        # 단위 없이 끝난 금액(예: '1조 2,345억' 뒤 '원')만 다음 숫자와 합칠 수 있다
        prev_end, prev_mult = m.end(), (mult if m["mult"] and not m["unit"] else None)
    return out


def parse_unit(unit: str | None) -> tuple[Decimal, str] | None:
    """표 단위 문자열 → (배수, 종류). '백만원' → (10^6, money), '%' → (1, percent)."""
    if not unit:
        return None
    u = unit.replace(" ", "")
    if u in ("%", "퍼센트"):
        return Decimal(1), "percent"
    if u.endswith("원"):
        prefix = u[:-1]
        if prefix == "":
            return Decimal(1), "money"
        if prefix in _MULTIPLIERS:
            return _MULTIPLIERS[prefix], "money"
    return None


def _checkable(q: Quantity) -> bool:
    # 횟수·순위·개수 같은 작은 정수는 오탐이 많아 검사하지 않는다
    return not (q.kind == "plain" and q.value < 100 and q.value == q.value.to_integral_value())


def matches(a: Quantity, s: Quantity) -> bool:
    tol = max(a.tol, s.tol)
    if a.kind == "money":
        return s.kind in ("money", "plain") and abs(abs(a.value) - abs(s.value)) <= tol
    if a.kind == "percent":
        return s.kind in ("percent", "plain") and abs(a.value - s.value) <= tol
    return abs(a.value - s.value) <= tol


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def unverified_numbers(sentence: str, sources: list[tuple[str, str | None]]) -> list[str]:
    """문장 속 숫자 중 출처(본문, 표 단위)에서 확인되지 않은 것."""
    found: list[Quantity] = []
    for body, unit in sources:
        found += extract(body)
        if unit:
            found += extract(body, unit)
    return [
        q.text for q in extract(sentence) if _checkable(q) and not any(matches(q, s) for s in found)
    ]


def quoted_spans(sentences: list[str], text: str, unit: str | None = None) -> list[tuple[int, int]]:
    """답변 문장들에 쓴 숫자가 원문 text 의 어디에 있는지 (시작, 끝) 위치.

    검사 규칙은 unverified_numbers 와 같다. 화면에서 답변이 옮긴 숫자를 원문에 표시하는 데 쓴다."""
    wanted = [q for s in sentences for q in extract(s) if _checkable(q)]
    if not wanted:
        return []
    candidates = extract(text) + (extract(text, unit) if unit else [])
    spans = {(s.start, s.end) for s in candidates if any(matches(q, s) for q in wanted)}
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def checked_count(sentence: str) -> int:
    """문장에서 원문과 대조하는 숫자 개수 (연도·작은 정수 제외)."""
    return sum(1 for q in extract(sentence) if _checkable(q))
