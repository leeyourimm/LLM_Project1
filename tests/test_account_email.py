"""계정 메일: 비밀번호 재설정, 가입 이메일 인증, 새 기기 로그인 알림. 메일은 가짜로 받는다."""

import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dartrag.config import Settings
from dartrag.doctor import check_account_mail
from dartrag.feed.channels import SendError
from dartrag.web import Services, auth, create_app
from dartrag.web.ratelimit import Rule
from tests.test_web import PW, FakeAnswerer, FakeRepo, FakeRetriever, FakeSender

CHROME_WIN = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CHROME_WIN_NEWER = CHROME_WIN.replace("131.0.0.0", "132.0.6834.83")
FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:132.0) Gecko/20100101 Firefox/132.0"


def make(*, email=True, required=False, limits=None):
    repo = FakeRepo()
    sender = FakeSender()

    @contextmanager
    def repo_cm():
        yield repo

    services = Services(
        repo_cm,
        lambda r: FakeAnswerer(),
        lambda r: FakeRetriever(),
        auth_required=True,
        senders=lambda: {"email": sender} if email else {},
        public_url="https://dart.example/",
        secret_key="k" * 32,
        email_verification_required=required,
        limits=limits or {},
    )
    app = create_app(services, auth.LoginLimiter(max_failures=3))
    return app, repo, sender


def browser(app, ua=CHROME_WIN) -> TestClient:
    return TestClient(app, headers={"user-agent": ua})


def token_from(body: str, path: str) -> str:
    link = next(w for w in body.split() if f"/{path}?token=" in w)
    assert link.startswith(f"https://dart.example/{path}?token=")  # 끝의 / 를 정리
    return link.split("token=")[1]


def signup(client, email="a@b.co"):
    r = client.post("/api/auth/signup", json={"email": email, "password": PW})
    assert r.status_code == 201
    return r


# --- 비밀번호 재설정 -----------------------------------------------------------


def test_password_reset_flow_revokes_sessions_and_is_single_use():
    app, repo, sender = make()
    laptop = browser(app)
    signup(laptop)
    phone = browser(app, FIREFOX_MAC)
    assert phone.post("/api/auth/login", json={"email": "a@b.co", "password": PW}).is_success
    sender.sent.clear()

    anon = browser(app)
    r = anon.post("/api/auth/password/forgot", json={"email": " A@B.co "})
    assert r.status_code == 202
    [(to, subject, body)] = sender.sent
    assert to == "a@b.co" and "재설정" in subject and "30분" in body
    token = token_from(body, "reset-password")
    # DB(가짜)에는 해시만, 30분 뒤 만료
    assert token not in repo.tokens and auth.token_hash(token) in repo.tokens
    uid, purpose, expires = repo.tokens[auth.token_hash(token)]
    assert purpose == "reset"
    assert timedelta(minutes=29) < expires - datetime.now(UTC) <= timedelta(minutes=30)

    # 정책에 걸리면 링크는 그대로 남아 다시 쓸 수 있다
    short = anon.post("/api/auth/password/reset", json={"token": token, "password": "short"})
    assert short.status_code == 422 and auth.token_hash(token) in repo.tokens

    new_pw = "brand new password 42"
    ok = anon.post("/api/auth/password/reset", json={"token": token, "password": new_pw})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "a@b.co"
    assert "dartrag_session=" in ok.headers["set-cookie"]
    assert anon.get("/api/companies").status_code == 200  # 바꾼 기기는 로그인된 상태
    # 다른 기기의 로그인은 모두 끊긴다
    assert laptop.get("/api/companies").status_code == 401
    assert phone.get("/api/companies").status_code == 401
    # 메일을 받았으니 이메일도 인증된 것으로 본다
    assert anon.get("/api/auth/me").json()["user"]["email_verified"] is True

    # 한 번만 쓸 수 있다
    again = anon.post("/api/auth/password/reset", json={"token": token, "password": "x" * 12})
    assert again.status_code == 400 and "만료" in again.json()["detail"]
    old = browser(app).post("/api/auth/login", json={"email": "a@b.co", "password": PW})
    assert old.status_code == 401
    new = browser(app).post("/api/auth/login", json={"email": "a@b.co", "password": new_pw})
    assert new.is_success


def test_reset_token_expires_and_only_latest_link_works():
    app, repo, sender = make()
    signup(browser(app))
    verify_token = token_from(sender.sent[0][2], "verify-email")
    sender.sent.clear()
    anon = browser(app)
    anon.post("/api/auth/password/forgot", json={"email": "a@b.co"})
    anon.post("/api/auth/password/forgot", json={"email": "a@b.co"})
    first, second = (token_from(body, "reset-password") for _, _, body in sender.sent)
    body = {"token": first, "password": "another password 1"}
    assert anon.post("/api/auth/password/reset", json=body).status_code == 400

    # 30분이 지나면 쓸 수 없다
    h = auth.token_hash(second)
    uid, purpose, _ = repo.tokens[h]
    repo.tokens[h] = (uid, purpose, datetime.now(UTC) - timedelta(seconds=1))
    body = {"token": second, "password": "another password 1"}
    assert anon.post("/api/auth/password/reset", json=body).status_code == 400
    assert h not in repo.tokens  # 만료된 것도 지운다
    bogus = anon.post("/api/auth/password/reset", json={"token": "x", "password": PW})
    assert bogus.status_code == 400
    # 가입 인증 링크의 토큰으로는 비밀번호를 바꿀 수 없다 (용도가 다름)
    wrong = anon.post("/api/auth/password/reset", json={"token": verify_token, "password": PW})
    assert wrong.status_code == 400
    assert browser(app).post("/api/auth/login", json={"email": "a@b.co", "password": PW}).is_success


def test_forgot_does_not_reveal_accounts():
    app, repo, sender = make()
    signup(browser(app))
    sender.sent.clear()
    anon = browser(app)
    known = anon.post("/api/auth/password/forgot", json={"email": "a@b.co"})
    ghost = anon.post("/api/auth/password/forgot", json={"email": "nobody@b.co"})
    assert known.status_code == ghost.status_code == 202
    assert known.json() == ghost.json()
    assert "가입된 계정이 있으면" in ghost.json()["message"]
    assert [to for to, *_ in sender.sent] == ["a@b.co"]  # 없는 주소로는 보내지 않는다
    bad = anon.post("/api/auth/password/forgot", json={"email": "not-an-email"})
    assert bad.status_code == 422


def test_forgot_rate_limits():
    # 접속 주소별 rate("auth")
    app, _, _ = make(limits={"auth": [Rule(2, 3600, "1시간에 2번")]})
    anon = browser(app)
    codes = [
        anon.post("/api/auth/password/forgot", json={"email": "z@b.co"}).status_code
        for _ in range(3)
    ]
    assert codes == [202, 202, 429]
    r = anon.post("/api/auth/password/reset", json={"token": "t", "password": PW})
    assert r.status_code == 429  # 같은 범위로 센다

    # 같은 주소로 메일이 쏟아지지 않게: 응답은 같고 메일만 3통에서 멈춘다
    app, _, sender = make()
    signup(browser(app))
    sender.sent.clear()
    anon = browser(app)
    replies = [anon.post("/api/auth/password/forgot", json={"email": "a@b.co"}) for _ in range(5)]
    assert {r.status_code for r in replies} == {202}
    assert len({r.text for r in replies}) == 1
    assert len(sender.sent) == 3


def test_reset_clears_login_lockout():
    app, _, sender = make()
    signup(browser(app))
    anon = browser(app)
    for _ in range(3):
        anon.post("/api/auth/login", json={"email": "a@b.co", "password": "wrong-password"})
    assert anon.post("/api/auth/login", json={"email": "a@b.co", "password": PW}).status_code == 429
    anon.post("/api/auth/password/forgot", json={"email": "a@b.co"})
    token = token_from(sender.sent[-1][2], "reset-password")
    new_pw = "fresh password 77"
    assert anon.post(
        "/api/auth/password/reset", json={"token": token, "password": new_pw}
    ).is_success
    anon.post("/api/auth/logout")
    assert anon.post("/api/auth/login", json={"email": "a@b.co", "password": new_pw}).is_success


def test_without_smtp_features_degrade():
    app, repo, _ = make(email=False, required=True)
    client = browser(app)
    signup(client)  # 메일 없이도 가입된다
    me = client.get("/api/auth/me").json()
    assert me["email_enabled"] is False
    # 메일이 없으면 인증할 방법이 없어 강제하지 않는다
    assert me["email_verification_required"] is False
    assert repo.tokens == {}
    r = browser(app).post("/api/auth/password/forgot", json={"email": "a@b.co"})
    assert r.status_code == 503 and "운영자" in r.json()["detail"]
    assert client.post("/api/auth/verify-email/resend").status_code == 503


def test_mail_failures_do_not_log_addresses_or_links(caplog):
    app, repo, sender = make()

    def boom(to, subject, body):
        raise SendError("이메일 전송 실패: SMTPAuthenticationError")

    sender.send = boom
    with caplog.at_level(logging.DEBUG):
        signup(browser(app))
        r = browser(app).post("/api/auth/password/forgot", json={"email": "a@b.co"})
    assert r.status_code == 202  # 메일이 실패해도 같은 응답
    text = caplog.text
    assert "메일 발송 실패" in text
    assert "a@b.co" not in text and "token" not in text and "dart.example" not in text


# --- 가입 이메일 인증 -----------------------------------------------------------


def test_signup_sends_verification_and_link_verifies():
    app, repo, sender = make()
    client = browser(app)
    r = signup(client)
    assert r.json()["user"] == {"email": "a@b.co", "email_verified": False}
    [(to, subject, body)] = sender.sent
    assert to == "a@b.co" and "인증" in subject
    token = token_from(body, "verify-email")
    me = client.get("/api/auth/me").json()
    assert me["email_enabled"] and me["user"]["email_verified"] is False
    assert me["email_verification_required"] is False  # 기본값: 강제하지 않음

    # 다른 기기(휴대폰 메일 앱)에서 로그인 없이 열어도 된다
    phone = browser(app, FIREFOX_MAC)
    assert phone.post("/api/auth/verify-email", json={"token": token}).json() == {"ok": True}
    assert client.get("/api/auth/me").json()["user"]["email_verified"] is True
    again = phone.post("/api/auth/verify-email", json={"token": token})
    assert again.status_code == 400 and "다시" in again.json()["detail"]
    assert repo.verified == {1}
    assert repo.tokens == {}


def test_verification_gates_email_alerts_when_required():
    app, repo, sender = make(required=True)
    client = browser(app)
    signup(client)
    token = token_from(sender.sent[0][2], "verify-email")
    me = client.get("/api/auth/me").json()
    assert me["email_verification_required"] is True and not me["user"]["email_verified"]
    # 로그인은 되지만 이메일 알림은 켤 수 없다
    assert client.get("/api/companies").status_code == 200
    alerts = client.get("/api/alerts").json()
    assert alerts["email_needs_verification"] is True
    r = client.post("/api/alerts/email")
    assert r.status_code == 403 and "인증" in r.json()["detail"]
    # 이 기능 전에 인증해 둔 채널이 꺼져 있는 경우
    repo.start_alert_channel(1, "email", "a@b.co", None, None)
    repo.channels[(1, "email")].update(verified=True, enabled=False)
    assert client.patch("/api/alerts/email", json={"enabled": True}).status_code == 403
    assert client.patch("/api/alerts/email", json={"enabled": False}).status_code == 200

    assert client.post("/api/auth/verify-email", json={"token": token}).is_success
    assert client.get("/api/alerts").json()["email_needs_verification"] is False
    assert client.patch("/api/alerts/email", json={"enabled": True}).status_code == 200
    assert client.post("/api/alerts/email").status_code == 202


def test_unverified_can_use_alerts_when_not_required():
    app, _, _ = make(required=False)
    client = browser(app)
    signup(client)
    assert client.get("/api/alerts").json()["email_needs_verification"] is False
    assert client.post("/api/alerts/email").status_code == 202


def test_alert_email_link_also_verifies_account():
    app, repo, sender = make(required=True)
    client = browser(app)
    signup(client)
    repo.verified.clear()
    # 이전부터 쓰던 알림 인증 링크도 가입 주소로 가므로 함께 인증된다
    repo.start_alert_channel(1, "email", "a@b.co", auth.token_hash("alert-code"), None)
    client.get("/api/alerts/email/verify?token=alert-code", follow_redirects=False)
    assert client.get("/api/auth/me").json()["user"]["email_verified"] is True


def test_resend_verification_is_rate_limited():
    app, repo, sender = make(limits={"auth": [Rule(3, 3600, "1시간에 3번")]})
    client = browser(app)
    signup(client)  # rate("auth") 1번
    first = token_from(sender.sent[0][2], "verify-email")
    r = client.post("/api/auth/verify-email/resend")
    assert r.status_code == 202 and r.json()["sent_to"] == "a@b.co"
    assert len(sender.sent) == 2
    again = client.post("/api/auth/verify-email/resend")
    assert again.status_code == 429 and again.headers["retry-after"] == "60"
    assert client.post("/api/auth/verify-email/resend").status_code == 429  # rate("auth") 초과
    assert len(sender.sent) == 2
    # 새 링크를 보내면 예전 링크는 쓸 수 없다
    assert client.post("/api/auth/verify-email", json={"token": first}).status_code == 429

    app, repo, sender = make()
    client = browser(app)
    signup(client)
    old = token_from(sender.sent[0][2], "verify-email")
    client.post("/api/auth/verify-email/resend")
    new = token_from(sender.sent[1][2], "verify-email")
    assert client.post("/api/auth/verify-email", json={"token": old}).status_code == 400
    assert client.post("/api/auth/verify-email", json={"token": new}).is_success
    done = client.post("/api/auth/verify-email/resend")
    assert done.json() == {"ok": True, "already_verified": True}
    assert TestClient(app).post("/api/auth/verify-email/resend").status_code == 401


# --- 새 기기 로그인 알림 ---------------------------------------------------------


def login(client, password=PW):
    r = client.post("/api/auth/login", json={"email": "a@b.co", "password": password})
    assert r.status_code == 200
    return r


def test_new_device_login_notice():
    app, repo, sender = make()
    signup(browser(app))  # 가입한 기기는 알리지 않고 기록만
    repo.mark_email_verified(1)
    sender.sent.clear()

    login(browser(app))  # 같은 브라우저·네트워크
    login(browser(app, CHROME_WIN_NEWER))  # 브라우저가 업데이트돼도 같은 기기
    assert sender.sent == []

    login(browser(app, FIREFOX_MAC))
    [(to, subject, body)] = sender.sent
    assert to == "a@b.co" and "새 기기" in subject
    assert "Firefox · macOS" in body and "https://dart.example/forgot-password" in body
    login(browser(app, FIREFOX_MAC))
    assert len(sender.sent) == 1  # 두 번째부터는 아는 기기

    # 원래 값(User-Agent, IP)은 저장하지 않고 해시만
    stored = repo.devices[1]
    assert len(stored) == 2 and all(len(h) == 64 for h in stored)
    assert not any("Firefox" in h or "testclient" in h for h in stored)


def test_new_device_notice_needs_verified_address():
    app, repo, sender = make()
    signup(browser(app))
    sender.sent.clear()
    login(browser(app, FIREFOX_MAC))  # 인증 전: 기록만 하고 보내지 않는다
    assert sender.sent == [] and len(repo.devices[1]) == 2


def test_legacy_account_first_login_is_not_alerted():
    app, repo, sender = make()
    signup(browser(app))
    repo.mark_email_verified(1)
    repo.devices.clear()  # 이 기능 전에 만든 계정
    sender.sent.clear()
    login(browser(app, FIREFOX_MAC))
    assert sender.sent == []
    login(browser(app))
    assert len(sender.sent) == 1


def test_device_fingerprint_helpers():
    assert auth.device_label(CHROME_WIN) == ("Chrome", "Windows")
    assert auth.device_label(FIREFOX_MAC) == ("Firefox", "macOS")
    edge = CHROME_WIN + " Edg/131.0.0.0"
    assert auth.device_label(edge) == ("Edge", "Windows")
    assert auth.device_label(None) == ("알 수 없는 브라우저", "알 수 없는 OS")
    assert auth.network_of("203.0.113.7") == "203.0.113.0/24"
    assert auth.network_of("::ffff:203.0.113.7") == "203.0.113.0/24"
    assert auth.network_of("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::/64"
    assert auth.network_of("testclient") == "알 수 없음"

    h = auth.device_hash("key", 1, CHROME_WIN, "203.0.113.7")
    assert h == auth.device_hash("key", 1, CHROME_WIN_NEWER, "203.0.113.99")
    assert h != auth.device_hash("key", 1, CHROME_WIN, "198.51.100.7")
    assert h != auth.device_hash("key", 2, CHROME_WIN, "203.0.113.7")
    assert h != auth.device_hash("other", 1, CHROME_WIN, "203.0.113.7")
    assert "203.0.113" not in h


# --- 점검 ----------------------------------------------------------------------


def settings(**kw):
    return Settings(_env_file=None, **kw)


@pytest.mark.parametrize(
    ("kw", "ok", "needle"),
    [
        ({}, True, "필요 없음"),
        ({"auth_required": True}, False, "dartrag user password"),
        (
            {"auth_required": True, "email_verification_required": True},
            False,
            "적용되지 않습니다",
        ),
        (
            {
                "auth_required": True,
                "smtp_host": "smtp.example",
                "smtp_from": "no-reply@example.com",
                "secret_key": "s" * 32,
            },
            True,
            "인증 선택",
        ),
        (
            {
                "auth_required": True,
                "smtp_host": "smtp.example",
                "smtp_from": "no-reply@example.com",
            },
            False,
            "SECRET_KEY",
        ),
    ],
)
def test_doctor_reports_account_mail(kw, ok, needle):
    check = check_account_mail(settings(**kw))
    assert check.ok is ok and needle in check.detail and check.required is False
    assert "smtp_password" not in check.detail.lower()
