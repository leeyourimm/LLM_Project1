import json
from datetime import date

import httpx
import pytest
import respx

from dartrag.dart.models import Filing
from dartrag.feed.events import classify
from dartrag.feed.notify import ConsoleNotifier, WebhookNotifier, format_alert
from dartrag.pipeline.feed import poll, send_alerts


@pytest.mark.parametrize(
    "name, type_, importance, correction",
    [
        ("주요사항보고서(유상증자결정)", "rights_issue", 3, False),
        ("주요사항보고서(유무상증자결정)", "rights_bonus_issue", 3, False),
        ("[기재정정]주요사항보고서(전환사채권발행결정)", "convertible_bond", 3, True),
        ("주요사항보고서(회사분할합병결정)", "split_merger", 3, False),
        ("주요사항보고서(회사합병결정)", "merger", 3, False),
        ("주요사항보고서(소송등의제기)", "litigation", 3, False),
        ("최대주주변경", "largest_shareholder_change", 3, False),
        ("주요사항보고서(자기주식취득결정)", "treasury_stock", 2, False),
        ("단일판매ㆍ공급계약체결", "supply_contract", 2, False),
        ("현금ㆍ현물배당결정", "dividend", 2, False),
        ("연결재무제표기준영업(잠정)실적(공정공시)", "preliminary_earnings", 2, False),
        ("주식등의대량보유상황보고서(일반)", "major_holding", 1, False),
        ("[첨부추가]기타경영사항(자율공시)", "other", 1, False),
    ],
)
def test_classify(name, type_, importance, correction):
    ev = classify(name)
    assert (ev.type, ev.importance, ev.correction) == (type_, importance, correction)


ROW = {
    "rcept_no": "20250311000001",
    "corp_name": "삼성전자",
    "report_nm": "주요사항보고서(유상증자결정)",
    "rcept_dt": date(2025, 3, 11),
    "event_label": "유상증자",
    "importance": 3,
    "correction": True,
}


def test_format_alert():
    text = format_alert(ROW)
    assert text.startswith("🔴 삼성전자 · 유상증자 (정정)\n")
    assert text.endswith("rcpNo=20250311000001")


@respx.mock
def test_webhook_formats_and_hides_url():
    slack = respx.post("https://hooks.slack.com/services/SECRET").respond(200)
    discord = respx.post("https://discord.com/api/webhooks/SECRET").respond(204)
    WebhookNotifier("https://hooks.slack.com/services/SECRET").send("안녕")
    WebhookNotifier("https://discord.com/api/webhooks/SECRET").send("안녕")
    assert json.loads(slack.calls[0].request.content) == {"text": "안녕"}
    assert json.loads(discord.calls[0].request.content) == {"content": "안녕"}

    respx.post("https://hooks.slack.com/services/BAD").respond(404, text="no_service SECRET")
    with pytest.raises(RuntimeError) as e:
        WebhookNotifier("https://hooks.slack.com/services/BAD").send("x")
    assert str(e.value) == "웹훅 전송 실패: HTTP 404"
    respx.post("https://hooks.slack.com/services/DOWN").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(RuntimeError, match="ConnectError") as e:
        WebhookNotifier("https://hooks.slack.com/services/DOWN").send("x")
    assert "DOWN" not in str(e.value)


def filing(no, name, cls="Y"):
    return Filing(
        corp_code="00126380",
        corp_name="삼성전자",
        stock_code="005930 ",
        corp_cls=cls,
        report_nm=name,
        rcept_no=f"2025031100000{no}",
        rcept_dt=date(2025, 3, 11),
    )


class FakeClient:
    def __init__(self):
        self.calls = []

    def iter_filings(self, corp_code, start, end, *, pblntf_ty, final_only):
        self.calls.append((corp_code, pblntf_ty, final_only))
        if pblntf_ty == "B":
            yield filing(1, "주요사항보고서(유상증자결정)")
            yield filing(2, "주요사항보고서(감자결정)", cls="E")  # 비상장
        else:
            yield filing(3, "단일판매ㆍ공급계약체결")


class FakeRepo:
    def __init__(self, pending=()):
        self.inserted = []
        self.pending = list(pending)
        self.notified = []

    def insert_disclosures(self, rows):
        self.inserted += rows
        return [r["rcept_no"] for r in rows[:1]]

    def pending_alerts(self, channel):
        return self.pending

    def mark_notified(self, rcept_no, channel):
        self.notified.append((rcept_no, channel))


def test_poll_classifies_and_filters_listed():
    client, repo = FakeClient(), FakeRepo()
    s = poll(client, repo, date(2025, 3, 11), date(2025, 3, 11))
    assert client.calls == [(None, "B", False), (None, "I", False)]
    assert (s.fetched, s.new) == (3, 1)
    assert [r["rcept_no"] for r in repo.inserted] == ["20250311000001", "20250311000003"]
    first = repo.inserted[0]
    assert first["stock_code"] == "005930" and first["event_type"] == "rights_issue"
    assert first["pblntf_ty"] == "B" and first["importance"] == 3

    repo = FakeRepo()
    poll(FakeClient(), repo, date(2025, 3, 11), date(2025, 3, 11), listed_only=False)
    assert len(repo.inserted) == 3


def test_send_alerts_marks_only_successful():
    repo = FakeRepo(pending=[ROW, {**ROW, "rcept_no": "20250311000009"}])
    sent_texts = []

    class Flaky:
        channel = "webhook"

        def send(self, text):
            if "20250311000009" in text:
                raise RuntimeError("웹훅 전송 실패: HTTP 500")
            sent_texts.append(text)

    sent, errors = send_alerts(repo, Flaky())
    assert sent == 1 and repo.notified == [("20250311000001", "webhook")]
    assert errors == ["20250311000009: 웹훅 전송 실패: HTTP 500"]

    out = []
    send_alerts(FakeRepo(pending=[ROW]), ConsoleNotifier(out.append))
    assert out and out[0].startswith("🔴")
