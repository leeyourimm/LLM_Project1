"""이어지는 질문의 맥락 해석.

"삼성전자 2024년 영업이익은?" 다음에 "그럼 전년은?" 이라고 물으면
회사·연도·주제를 앞 질문에서 이어받아 "삼성전자 2023년 영업이익" 으로 바꾼다.
규칙 기반이라 어떻게 해석했는지 화면에 그대로 보여줄 수 있다.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from dartrag.finance.query import _find_years, find_companies, normalize_name

# 앞 질문의 연도 기준 상대 표현
RELATIVE_TO_CONTEXT = [
    ("그 전해", -1),
    ("그전해", -1),
    ("전년도", -1),
    ("전년", -1),
    ("다음 해", 1),
]
# 오늘 기준 상대 표현
RELATIVE_TO_TODAY = [("재작년", -2), ("작년", -1), ("지난해", -1), ("올해", 0), ("금년", 0)]

_FILLER_RE = re.compile(
    r"^(그럼|그러면|그렇다면|그리고|또|그건|그거|그때|그때는|거기|여기서|이번엔|이번에는)\s*"
)
_PARTICLE_RE = re.compile(r"(은|는|이|가|도|의|에는|에서는|으로는|로는|요|이요)?\s*[?？.!]*\s*$")
_YEAR_TEXT_RE = re.compile(
    r"(?:19|20)\d{2}\s*년?\s*(?:~|-|부터|에서)\s*(?:19|20)\d{2}\s*년?|(?:19|20)\d{2}\s*년?"
    r"|(?<!\d)\d{2}\s*년"
)


@dataclass
class TurnContext:
    corp_codes: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    topic: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "TurnContext | None":
        if not d:
            return None
        return cls(list(d.get("corp_codes", [])), list(d.get("years", [])), d.get("topic", ""))


@dataclass
class Resolved:
    question: str  # 검색·답변에 쓰는 완전한 질문
    context: TurnContext
    inherited: list[str] = field(default_factory=list)  # 이어받은 것: 회사, 연도, 주제


def _relative_years(text: str, base: int | None, today: date) -> list[int]:
    for word, delta in RELATIVE_TO_CONTEXT:
        if word in text and base is not None:
            return [base + delta]
    for word, delta in RELATIVE_TO_TODAY:
        if word in text:
            return [today.year + delta]
    return []


def _topic(text: str, names: list[str]) -> str:
    """회사명·연도·상대 표현·군말을 지운 나머지 = 무엇을 묻는지."""
    t = text
    for name in sorted(names, key=len, reverse=True):
        t = re.sub(re.escape(name), " ", t, flags=re.I)
    t = _YEAR_TEXT_RE.sub(" ", t)
    for word, _ in RELATIVE_TO_CONTEXT + RELATIVE_TO_TODAY:
        t = t.replace(word, " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = _FILLER_RE.sub("", t).strip()
    t = _PARTICLE_RE.sub("", t).strip()
    return t


def _year_phrase(years: list[int]) -> str:
    if not years:
        return ""
    if len(years) == 1:
        return f"{years[0]}년"
    return f"{years[0]}~{years[-1]}년"


def resolve(
    question: str,
    previous: TurnContext | None,
    companies: list[tuple[str, str]],
    *,
    today: date | None = None,
) -> Resolved:
    today = today or date.today()
    names = dict(companies)
    corp_codes = find_companies(question, companies)
    mentioned = [names[c] for c in corp_codes]
    base_year = previous.years[-1] if previous and previous.years else None
    years = _find_years(question) or _relative_years(question, base_year, today)
    # 약칭으로 쓴 회사명도 주제에서 빼기 위해 정규화된 이름 비교도 함께 쓴다
    topic = _topic(question, mentioned + [normalize_name(n) for n in mentioned])

    inherited: list[str] = []
    if previous:
        if not corp_codes and previous.corp_codes:
            corp_codes = list(previous.corp_codes)
            inherited.append("회사")
        if not years and previous.years and (len(topic) < 2 or "회사" in inherited):
            years = list(previous.years)
            inherited.append("연도")
        if len(topic) < 2 and previous.topic:
            topic = previous.topic
            inherited.append("주제")

    context = TurnContext(corp_codes, years, topic)
    if not inherited and not _relative_years(question, base_year, today):
        return Resolved(question, context)
    parts = [", ".join(names.get(c, c) for c in corp_codes), _year_phrase(years), topic]
    text = " ".join(p for p in parts if p).strip()
    return Resolved(f"{text}?" if text else question, context, inherited)
