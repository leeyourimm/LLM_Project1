"""질문에서 재무 조회 조건(회사, 연도, 보고서, 지표)을 뽑는다.

규칙 기반으로 먼저 처리한다. 로컬 LLM 의 도구 호출보다 예측 가능하고,
해석 결과(어떤 회사·연도·지표를 조회했는지)를 답변에 그대로 보여줄 수 있다.
"""

import re
from dataclasses import dataclass, field

from dartrag.finance.accounts import GROWTH_KEYWORDS, METRICS, RATIOS

# 보고서 코드: 사업보고서 11011, 반기 11012, 1분기 11013, 3분기 11014
PERIOD_PATTERNS = [
    (re.compile(r"1\s*분기|1Q", re.I), "11013", "1분기"),
    (re.compile(r"반기|상반기|2\s*분기|2Q", re.I), "11012", "반기"),
    (re.compile(r"3\s*분기|3Q", re.I), "11014", "3분기"),
]
ANNUAL = ("11011", "연간")

_YEAR_RANGE_RE = re.compile(r"((?:19|20)\d{2})\s*(?:년)?\s*(?:~|-|부터|에서)\s*((?:19|20)\d{2})")
_YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*년?|(?<!\d)(\d{2})\s*년")

# corpCode.xml 의 한글 표기와 흔한 영문 표기를 같게 본다
_NAME_ALIASES = [
    ("에스케이", "sk"),
    ("엘지", "lg"),
    ("케이티", "kt"),
    ("씨제이", "cj"),
    ("지에스", "gs"),
]


def normalize_name(name: str) -> str:
    n = re.sub(r"\(주\)|주식회사|\s", "", name).lower()
    for ko, en in _NAME_ALIASES:
        n = n.replace(ko, en)
    return n


@dataclass
class FinanceQuery:
    corp_codes: list[str]
    metrics: list[str]
    ratios: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)  # 비어 있으면 최신 연도
    reprt_code: str = ANNUAL[0]
    period_label: str = ANNUAL[1]
    growth: bool = False

    @property
    def needed_metrics(self) -> list[str]:
        keys = list(self.metrics)
        for r in self.ratios:
            for k in (RATIOS[r].numerator, RATIOS[r].denominator):
                if k not in keys:
                    keys.append(k)
        return keys


def _find_keywords(text: str) -> tuple[list[str], list[str]]:
    """긴 키워드부터 찾고 찾은 자리는 지워서 '영업이익률' 이 '영업이익' 으로도 잡히지 않게 한다."""
    candidates = [(kw, "ratio", r.key) for r in RATIOS.values() for kw in r.keywords]
    candidates += [(kw, "metric", m.key) for m in METRICS.values() for kw in m.keywords]
    candidates.sort(key=lambda c: len(c[0]), reverse=True)
    metrics: list[str] = []
    ratios: list[str] = []
    lowered = text.lower()
    for kw, kind, key in candidates:
        if kw in lowered:
            lowered = lowered.replace(kw, " ")
            target = ratios if kind == "ratio" else metrics
            if key not in target:
                target.append(key)
    return metrics, ratios


def _find_years(text: str) -> list[int]:
    if m := _YEAR_RANGE_RE.search(text):
        a, b = sorted((int(m[1]), int(m[2])))
        return list(range(a, b + 1))[:10]
    years = set()
    for m in _YEAR_RE.finditer(text):
        years.add(int(m[1]) if m[1] else 2000 + int(m[2]))
    return sorted(years)


def find_companies(text: str, companies: list[tuple[str, str]]) -> list[str]:
    """질문에 나온 회사명 → corp_code (질문에 나온 순서).

    긴 이름부터 찾아 '삼성전자우' 같은 겹침을 피한다. 찾은 자리는 같은 길이의 빈칸으로 지워
    다른 이름의 위치가 밀리지 않게 하고, 결과는 그 위치 순으로 돌려준다
    (비교 화면에서 고른 순서가 출처의 회사 순서와 같도록)."""
    norm = normalize_name(text)
    found = []
    for code, name in sorted(companies, key=lambda c: len(c[1]), reverse=True):
        key = normalize_name(name)
        if len(key) >= 2 and (pos := norm.find(key)) >= 0:
            norm = norm.replace(key, " " * len(key))
            found.append((pos, code))
    return [code for _, code in sorted(found)]


def parse_question(
    question: str, companies: list[tuple[str, str]], default_corp_codes: list[str]
) -> FinanceQuery | None:
    """재무 수치 질문이면 조회 조건을, 아니면 None."""
    metrics, ratios = _find_keywords(question)
    if not metrics and not ratios:
        return None
    corp_codes = find_companies(question, companies) or list(default_corp_codes)
    if not corp_codes:
        return None
    reprt_code, label = ANNUAL
    for pattern, code, plabel in PERIOD_PATTERNS:
        if pattern.search(question):
            reprt_code, label = code, plabel
            break
    years = _find_years(question)
    growth = any(k in question for k in GROWTH_KEYWORDS)
    if growth and len(years) == 1:
        years = [years[0] - 1, years[0]]  # 증감은 전년과 비교
    return FinanceQuery(corp_codes, metrics, ratios, years, reprt_code, label, growth)
