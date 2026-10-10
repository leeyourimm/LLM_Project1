import smtplib
from datetime import date

import httpx
import pytest
import respx

from dartrag.feed.alerts import (
    MAX_ITEMS,
    check_unsubscribe,
    format_bundle,
    send_user_alerts,
    unsubscribe_link,
    unsubscribe_token,
)
from dartrag.feed.channels import EmailSender, SendError, TelegramSender
from dartrag.feed.notify import EmailNotifier, TelegramNotifier
from dartrag.feed.telegram_bot import handle_update, token_hash

TOKEN = "123456:SECRET-bot-token"


def row(n, user=1, kind="email", target="a@b.co", corp="삼성전자", importance=3):
    return {
        "user_id": user,
        "kind": kind,
        "target": target,
        "rcept_no": f"2025031100000{n}",
        "corp_code": "00126380",
        "corp_name": corp,
        "report_nm": "주요사항보고서(유상증자결정)",
        "rcept_dt": date(2025, 3, 11),
        "event_label": "유상증자",
        "importance": importance,
        "correction": False,
    }


# --- 발송 수단 ---------------------------------------------------------------


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout):
        self.calls = [("connect", host, port)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, user, password):
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad password hunter2")
        self.calls.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.sent.append((self.calls, msg))


def test_email_sender_headers_and_errors():
    FakeSMTP.sent = []
    s = EmailSender(
        "smtp.example.com",
        username="me",
        password="hunter2",
        sender="alerts@example.com",
        smtp_factory=FakeSMTP,
    )
    s.send("a@b.co", "제목", "본문", "https://x/unsub?t=1")
    calls, msg = FakeSMTP.sent[0]
    assert calls == [("connect", "smtp.example.com", 587), "starttls", ("login", "me")]
    assert msg["To"] == "a@b.co" and msg["Subject"] == "제목"
    assert msg["List-Unsubscribe"] == "<https://x/unsub?t=1>"
    assert "그만 받기: https://x/unsub?t=1" in msg.get_content()
    FakeSMTP.fail = True
    try:
        with pytest.raises(SendError) as e:
            s.send("a@b.co", "제목", "본문")
    finally:
        FakeSMTP.fail = False
    assert "hunter2" not in str(e.value) and "SMTPAuthenticationError" in str(e.value)


@respx.mock
def test_telegram_sender_hides_token():
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    t = TelegramSender(TOKEN)
    t.send("42", "x" * 5000)
    body = route.calls[0].request.content.decode()
    assert '"chat_id":"42"' in body.replace(" ", "")
    assert len(httpx.Response(200, content=route.calls[0].request.content).json()["text"]) == 4096
    respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(403, json={"ok": False, "description": "bot was blocked"})
    )
    with pytest.raises(SendError) as e:
        t.send("42", "hi")
    assert "403" in str(e.value) and "blocked" in str(e.value) and TOKEN not in str(e.value)


def test_operator_notifiers():
    sent = []

    class S:
        def send(self, *a):
            sent.append(a)

    TelegramNotifier(S(), "99").send("🔴 삼성전자 · 유상증자\n...")
    EmailNotifier(S(), "me@x.co").send("🔴 삼성전자 · 유상증자\n...")
    assert sent[0] == ("99", "🔴 삼성전자 · 유상증자\n...")
    assert sent[1][0] == "me@x.co" and sent[1][1] == "[DART 알림] 삼성전자 · 유상증자"


# --- 사용자별 알림 -----------------------------------------------------------


def test_format_bundle_limits_and_headlines():
    items = [row(i % 10, corp="삼성전자" if i < 3 else "SK하이닉스") for i in range(12)]
    subject, body = format_bundle(items, {items[0]["rcept_no"]: ["관세 위험 추가"]})
    assert subject == "[DART 알림] 삼성전자 외 1곳 공시 12건"
    assert body.count("dart.fss.or.kr") == MAX_ITEMS
    assert "주요 변경점:\n  - 관세 위험 추가" in body and "외 2건" in body
    assert body.endswith("투자 권유가 아닙니다.")


class Repo:
    def __init__(self, rows):
        self.rows = rows
        self.marked = []

    def user_pending_alerts(self, focus=None):
        return self.rows

    def latest_diff_summary_for(self, rcept_no):
        return None

    def mark_user_notified(self, user_id, rcept_nos, channel):
        self.marked.append((user_id, rcept_nos, channel))


class Sender:
    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = fail_for

    def send(self, target, *args):
        if target in self.fail_for:
            raise SendError("이메일 전송 실패: SMTPRecipientsRefused")
        self.sent.append((target, *args))


def test_send_user_alerts_bundles_per_user_and_channel():
    rows = [
        row(1),
        row(2),
        row(1, user=2, target="bad@x.co"),
        row(1, user=2, kind="telegram", target="777"),
        row(3, user=3, kind="telegram", target="888"),
    ]
    repo, email, tg = Repo(rows), Sender(fail_for={"bad@x.co"}), Sender()
    run = send_user_alerts(
        repo,
        {"email": email, "telegram": tg},
        unsubscribe_url=lambda uid, kind: f"https://x/u/{uid}/{kind}",
    )
    assert (run.sent, run.items) == (3, 4)
    assert len(run.errors) == 1 and "user 2 email" in run.errors[0]
    assert email.sent[0][0] == "a@b.co" and email.sent[0][3] == "https://x/u/1/email"
    assert email.sent[0][1].endswith("공시 2건")
    assert [t[0] for t in tg.sent] == ["777", "888"]
    assert ("2", "email") not in {(str(u), c) for u, _, c in repo.marked}
    assert (1, ["20250311000001", "20250311000002"], "email") in repo.marked
    # 설정되지 않은 채널은 건너뛰고 보낸 것으로 치지도 않는다
    repo = Repo([row(1)])
    assert send_user_alerts(repo, {}).sent == 0 and repo.marked == []


def test_unsubscribe_tokens():
    t = unsubscribe_token("s3cret", 5, "email")
    assert check_unsubscribe("s3cret", 5, "email", t)
    assert not check_unsubscribe("s3cret", 6, "email", t)
    assert not check_unsubscribe("", 5, "email", t)
    assert unsubscribe_link("https://dart.example/", "s3cret")(5, "email") == (
        f"https://dart.example/api/alerts/unsubscribe?u=5&k=email&t={t}"
    )
    assert unsubscribe_link("https://dart.example", "")(5, "email") is None


# --- 텔레그램 봇 -------------------------------------------------------------


class BotRepo:
    def __init__(self):
        self.confirmed = []
        self.disabled = []

    def confirm_alert_channel(self, kind, hashed, target=None):
        self.confirmed.append((kind, hashed, target))
        return 1 if hashed == token_hash("good") else None

    def disable_telegram_chat(self, chat_id):
        self.disabled.append(chat_id)
        return 1


def update(text, chat_type="private"):
    return {"update_id": 1, "message": {"text": text, "chat": {"id": 42, "type": chat_type}}}


def test_telegram_bot_links_and_stops():
    repo, sender = BotRepo(), Sender()
    assert handle_update(repo, update("/start good"), sender).startswith("연결됐습니다")
    assert repo.confirmed == [("telegram", token_hash("good"), "42")]
    assert "만료" in handle_update(repo, update("/start bad"), sender)
    assert "멈췄습니다" in handle_update(repo, update("/stop"), sender)
    assert repo.disabled == ["42"]
    assert "1:1" in handle_update(repo, update("/start good", "group"), sender)
    assert handle_update(repo, {"update_id": 2}, sender) is None
    assert sender.sent[0][0] == "42"
