"""검색 전에 걸러야 할 질문: 투자 권유 요청, 프롬프트 인젝션.

이런 질문은 LLM 에 보내지 않고 정해진 안내로 답한다.
모델의 판단에 맡기지 않으니 항상 같은 방식으로 거절된다.
"""

import re
import unicodedata
from dataclasses import dataclass

ADVICE_PATTERNS = [
    r"(사|팔|매수하|매도하|들어가|투자하)(야|아야|어야)\s*(할까|해|하나|돼|되나|될까)",
    r"(살까|팔까|사도\s*(돼|될까|되나)|팔아도\s*(돼|될까|되나))",
    r"(매수|매도|손절|익절|물타기)\s*(추천|타이밍|시점|해도|할까|하는\s*게)",
    r"(목표\s*주가|적정\s*주가|주가\s*(전망|예측|오를|떨어질|오를까|떨어질까))",
    r"(오를까|떨어질까|상승할까|하락할까|급등|대박|수익률\s*(예상|전망))",
    r"(추천\s*(종목|주식)|어떤\s*(종목|주식)을?\s*(사|살))",
]
INJECTION_PATTERNS = [
    r"(이전|앞의|위의|모든)\s*(지시|명령|규칙|프롬프트)\S*\s*(무시|잊)",
    r"(ignore|disregard)\s+(all\s+|the\s+)?(previous|above|prior)\s+(instructions|prompts?)",
    r"(시스템|system)\s*(프롬프트|prompt|메시지|지시)[^\n]{0,12}?(보여|알려|출력|공개|print|reveal|show)",
    r"(너는|당신은)\s*이제\s*(부터)?\s*.{0,20}(이다|야|입니다)\s*[.!]?\s*(규칙|제한)",
    r"(jailbreak|DAN\s*mode|developer\s*mode)",
]

_ADVICE = [re.compile(p, re.I) for p in ADVICE_PATTERNS]
_INJECTION = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

ADVICE_REPLY = (
    "매수·매도 추천이나 주가 전망은 드리지 않아요. 이 서비스는 공시에 적힌 사실만 정리해 드립니다. "
    "대신 이런 질문은 답할 수 있어요: 최근 3년 매출과 영업이익 추이, "
    "사업보고서의 주요 리스크 요인, 최근 주요사항보고서(유상증자, 최대주주 변경 등)."
)
INJECTION_REPLY = (
    "이 요청은 처리할 수 없어요. 공시 내용에 대한 질문을 해 주세요. "
    "예: 삼성전자 2024년 영업이익은? / SK하이닉스 사업보고서의 주요 리스크는?"
)


@dataclass(frozen=True)
class Verdict:
    kind: str  # advice / injection
    reply: str


def normalize(question: str) -> str:
    """전각 문자(ｉｇｎｏｒｅ)나 보이지 않는 문자(zero-width space 등)로 규칙을 피하지 못하게."""
    text = unicodedata.normalize("NFKC", question)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", text)


def check_question(question: str) -> Verdict | None:
    question = normalize(question)
    if any(p.search(question) for p in _INJECTION):
        return Verdict("injection", INJECTION_REPLY)
    if any(p.search(question) for p in _ADVICE):
        return Verdict("advice", ADVICE_REPLY)
    return None
