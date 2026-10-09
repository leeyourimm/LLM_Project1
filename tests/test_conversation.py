from datetime import date

from dartrag.answer.conversation import TurnContext, resolve

COMPANIES = [("00126380", "삼성전자"), ("00164779", "SK하이닉스"), ("00126186", "삼성SDI")]
TODAY = date(2026, 10, 9)


def ask(q, prev=None):
    return resolve(q, prev, COMPANIES, today=TODAY)


def test_standalone_question_is_kept():
    r = ask("삼성전자 2024년 영업이익은?")
    assert r.question == "삼성전자 2024년 영업이익은?" and r.inherited == []
    assert r.context == TurnContext(["00126380"], [2024], "영업이익")


def test_follow_up_year_inherits_company_and_topic():
    first = ask("삼성전자 2024년 영업이익은?").context
    r = ask("그럼 전년은?", first)
    assert r.question == "삼성전자 2023년 영업이익?"
    assert r.inherited == ["회사", "주제"]
    r = ask("2022년은요?", first)
    assert r.question == "삼성전자 2022년 영업이익?"


def test_follow_up_company_inherits_year_and_topic():
    first = ask("삼성전자 2024년 영업이익은?").context
    r = ask("SK하이닉스는?", first)
    assert r.question == "SK하이닉스 2024년 영업이익?"
    assert r.context.corp_codes == ["00164779"] and set(r.inherited) == {"연도", "주제"}


def test_new_topic_keeps_company_and_year():
    first = ask("삼성전자 2024년 영업이익은?").context
    r = ask("주요 리스크 요인은 뭐야?", first)
    assert r.question == "삼성전자 2024년 주요 리스크 요인은 뭐야?"
    assert r.context.topic == "주요 리스크 요인은 뭐야"


def test_relative_to_today_and_no_context():
    assert ask("삼성전자 작년 매출").question == "삼성전자 2025년 매출?"
    r = ask("그럼 전년은?")  # 앞 질문이 없으면 해석할 수 없어 그대로
    assert r.question == "그럼 전년은?" and r.context.years == []


def test_context_roundtrip():
    c = TurnContext(["00126380"], [2023, 2024], "매출")
    assert TurnContext.from_dict(c.to_dict()) == c and TurnContext.from_dict(None) is None
