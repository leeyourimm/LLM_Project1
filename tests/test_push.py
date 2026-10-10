"""웹 푸시: 내용 암호화(RFC 8291), VAPID(RFC 8292), 발송 채널, 알림 묶음, API, 키 만들기 명령."""

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import date

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dartrag import cli
from dartrag.config import Settings
from dartrag.doctor import check_alerts
from dartrag.feed import webpush
from dartrag.feed.alerts import format_push, send_push, send_user_alerts
from dartrag.feed.channels import PushGone, SendError, WebPushSender
from dartrag.obs.errors import scrub_event
from dartrag.web import Services, auth, create_app
from tests.test_web import PW, FakeAnswerer, FakeRepo, FakeRetriever

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123:APA91-secret-part"


def browser_keys():
    """(브라우저 비밀키 객체, p256dh, auth) — 실제 브라우저가 만드는 것과 같은 모양."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key, webpush.public_key_of(key), webpush.b64url(os.urandom(16))


def decrypt(body: bytes, ua_key, auth: str) -> bytes:
    """브라우저 쪽 복호화 (RFC 8291 + RFC 8188). 테스트에서 서버 암호화를 거꾸로 확인한다."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_raw, ciphertext = body[21 : 21 + idlen], body[21 + idlen :]
    assert rs == webpush.RECORD_SIZE and len(ciphertext) <= rs
    ua_raw = webpush.b64url_decode(webpush.public_key_of(ua_key))
    shared = ua_key.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_raw)
    )
    ikm = webpush._hkdf(
        webpush.b64url_decode(auth), shared, b"WebPush: info\x00" + ua_raw + as_raw, 32
    )
    cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")  # 마지막 레코드 구분 바이트
    return plain[:-1]


# --- 암호화와 VAPID ----------------------------------------------------------------


def test_rfc8291_example():
    """RFC 8291 부록 A 의 예시 값 그대로."""
    as_key = webpush.load_private_key("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw")
    assert webpush.public_key_of(as_key) == (
        "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
    )
    body = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        salt=webpush.b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
        server_key=as_key,
    )
    assert webpush.b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocIn"
        "mYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNW"
        "QexSgSxsj_Qulcy4a-fN"
    )
    ua_key = webpush.load_private_key("q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94")
    assert decrypt(body, ua_key, "BTBZMqHH6r4Tts7J_aSIgg") == (
        b"When I grow up, I want to be a watermelon"
    )


def test_encrypt_is_fresh_each_time_and_limits_size():
    ua_key, p256dh, auth_secret = browser_keys()
    a = webpush.encrypt(b"hello", p256dh, auth_secret)
    b = webpush.encrypt(b"hello", p256dh, auth_secret)
    assert a != b and a[:16] != b[:16]  # 보낼 때마다 새 salt 와 서버 임시 키
    assert decrypt(a, ua_key, auth_secret) == decrypt(b, ua_key, auth_secret) == b"hello"
    with pytest.raises(webpush.PushKeyError):
        webpush.encrypt(b"x" * (webpush.MAX_PLAINTEXT + 1), p256dh, auth_secret)


def test_vapid_header_is_valid_es256_jwt():
    private, public = webpush.generate_vapid_keys()
    key = webpush.load_private_key(private)
    header = webpush.vapid_authorization(ENDPOINT, key, "mailto:ops@example.com", now=1000)
    assert header.startswith("vapid t=") and header.endswith(f", k={public}")
    token = header.removeprefix("vapid t=").split(", k=")[0]
    head, claims, sig = token.split(".")
    assert json.loads(webpush.b64url_decode(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(webpush.b64url_decode(claims)) == {
        "aud": "https://fcm.googleapis.com",  # 주소 경로(구독 비밀)는 넣지 않는다
        "exp": 1000 + 12 * 3600,
        "sub": "mailto:ops@example.com",
    }
    raw = webpush.b64url_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), webpush.b64url_decode(public)
    )
    pub.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_vapid_keys_and_sender_pairing():
    private, public = webpush.generate_vapid_keys()
    assert len(webpush.b64url_decode(private)) == 32
    assert webpush.b64url_decode(public)[0] == 4 and len(webpush.b64url_decode(public)) == 65
    assert WebPushSender(private, public, "mailto:a@b.co").public_key == public
    other_private, _ = webpush.generate_vapid_keys()
    with pytest.raises(ValueError) as e:
        WebPushSender(other_private, public, "mailto:a@b.co")
    assert other_private not in str(e.value) and public not in str(e.value)
    with pytest.raises(ValueError) as e:
        WebPushSender("not-a-key", public, "x")
    assert "not-a-key" not in str(e.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",
        "https://web.push.apple.com/QOabc",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
        "https://FCM.googleapis.com:443/fcm/send/abc",
    ],
)
def test_known_push_services_are_accepted(url):
    assert webpush.check_endpoint(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://fcm.googleapis.com/fcm/send/abc",  # https 만
        "https://fcm.googleapis.com.evil.example/x",  # 비슷한 이름
        "https://evilfcm.googleapis.com/x",
        "https://127.0.0.1/x",  # 내부망·자기 자신
        "https://api:8000/api/health",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pw@fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x",
        "https://fcm.googleapis.com/x y",
        "https://fcm.googleapis.com/" + "a" * 3000,
        "",
        "javascript:alert(1)",
    ],
)
def test_other_endpoints_are_rejected(url):
    with pytest.raises(webpush.PushKeyError):
        webpush.check_endpoint(url)


def test_subscription_key_checks():
    _, p256dh, auth_secret = browser_keys()
    webpush.check_subscription_keys(p256dh, auth_secret)
    bad_point = webpush.b64url(b"\x04" + b"\x01" * 64)  # 곡선 위의 점이 아님
    for p, a in [
        (bad_point, auth_secret),
        (p256dh[:-4], auth_secret),
        (p256dh, webpush.b64url(b"x" * 8)),
        ("***", auth_secret),
    ]:
        with pytest.raises(webpush.PushKeyError):
            webpush.check_subscription_keys(p, a)


# --- 발송 채널 ---------------------------------------------------------------------


def sender():
    private, public = webpush.generate_vapid_keys()
    return WebPushSender(private, public, "https://dart.example")


@respx.mock
def test_web_push_sender_encrypts_and_hides_endpoint():
    ua_key, p256dh, auth_secret = browser_keys()
    sub = {"endpoint": ENDPOINT, "p256dh": p256dh, "auth": auth_secret}
    s = sender()
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(201))
    message = {"title": "삼성전자 공시 1건", "body": "🔴 삼성전자 · 유상증자", "url": "/feed"}
    s.send(sub, message)
    req = route.calls[0].request
    assert req.headers["content-encoding"] == "aes128gcm"
    assert req.headers["ttl"] == "86400"
    assert req.headers["authorization"].startswith("vapid t=")
    assert f"k={s.public_key}" in req.headers["authorization"]
    assert json.loads(decrypt(req.content, ua_key, auth_secret)) == message
    assert "삼성전자".encode() not in req.content  # 본문은 암호문

    for status, error in [(410, PushGone), (404, PushGone), (500, SendError), (301, SendError)]:
        respx.post(ENDPOINT).mock(return_value=httpx.Response(status, text=ENDPOINT))
        with pytest.raises(error) as e:
            s.send(sub, message)
        assert str(status) in str(e.value) and "abc123" not in str(e.value)
    respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError(f"cannot reach {ENDPOINT}"))
    with pytest.raises(SendError) as e:
        s.send(sub, message)
    assert type(e.value) is SendError and "abc123" not in str(e.value)

    # 저장된 구독이 규칙에 맞지 않으면 보내지 않고 지울 대상으로 알린다
    with pytest.raises(PushGone):
        s.send(sub | {"endpoint": "https://127.0.0.1/x"}, message)


def test_sentry_scrubs_push_endpoints():
    event = {"exception": {"values": [{"value": f"POST {ENDPOINT} failed"}]}}
    scrubbed = scrub_event(event)["exception"]["values"][0]["value"]
    assert "abc123" not in scrubbed and "https://fcm.googleapis.com/" in scrubbed


# --- 알림 묶음 ----------------------------------------------------------------------


def row(n, corp="삼성전자", importance=3, correction=False):
    return {
        "user_id": 1,
        "kind": "push",
        "target": None,
        "rcept_no": f"2025031100000{n}",
        "corp_code": "00126380",
        "corp_name": corp,
        "report_nm": "주요사항보고서(유상증자결정)",
        "rcept_dt": date(2025, 3, 11),
        "event_label": "유상증자",
        "importance": importance,
        "correction": correction,
    }


def test_format_push_is_short_and_secret_free():
    one = format_push([row(1, correction=True)])
    assert one == {
        "title": "삼성전자 공시 1건",
        "body": "🔴 삼성전자 · 유상증자 (정정)",
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250311000001",
    }
    many = format_push([row(i, corp="SK하이닉스" if i > 1 else "삼성전자") for i in range(1, 6)])
    assert many["title"] == "삼성전자 외 1곳 공시 5건" and many["url"] == "/feed"
    assert many["body"].count("\n") == 3 and many["body"].endswith("… 외 2건")
    assert len(json.dumps(many, ensure_ascii=False).encode()) < 1000
    assert "unsubscribe" not in json.dumps(many) and "t=" not in json.dumps(many)


class FakePush:
    def __init__(self, public_key="PUB", gone=(), fail=()):
        self.public_key = public_key
        self.gone, self.fail = set(gone), set(fail)
        self.sent = []

    def send(self, sub, message):
        if sub["endpoint"] in self.gone:
            raise PushGone("웹 푸시 구독 만료: HTTP 410")
        if sub["endpoint"] in self.fail:
            raise SendError("웹 푸시 전송 실패: HTTP 500")
        self.sent.append((sub["endpoint"], message))


class AlertRepo:
    def __init__(self, subs):
        self.subs = subs
        self.notified = []
        self.results = []

    def user_pending_alerts(self, focus=None):
        return [row(1), row(2)]

    def latest_diff_summary_for(self, rcept_no):
        return None

    def push_subscriptions(self, user_id):
        return [s for s in self.subs if s["id"] not in {g for r in self.results for g in r[2]}]

    def record_push_results(self, user_id, sent, gone):
        self.results.append((user_id, sent, gone))

    def mark_user_notified(self, user_id, rcept_nos, channel):
        self.notified.append((user_id, rcept_nos, channel))


def sub(i, endpoint, key="PUB"):
    return {"id": i, "endpoint": endpoint, "p256dh": "k", "auth": "a", "vapid_key": key}


def test_send_user_alerts_push_removes_expired_and_marks_sent():
    repo = AlertRepo(
        [
            sub(1, "https://fcm.googleapis.com/a"),
            sub(2, "https://fcm.googleapis.com/gone"),
            sub(3, "https://fcm.googleapis.com/old-key", key="OLD"),
            sub(4, "https://fcm.googleapis.com/down"),
        ]
    )
    push = FakePush(
        gone={"https://fcm.googleapis.com/gone"}, fail={"https://fcm.googleapis.com/down"}
    )
    run = send_user_alerts(repo, {"push": push})
    assert run.sent == 1 and run.items == 2 and run.errors == []
    [(endpoint, message)] = push.sent
    assert endpoint.endswith("/a") and message["title"] == "삼성전자 공시 2건"
    # 끝난 구독과 예전 서버 키로 만든 구독은 지우고, 잠깐 실패한 구독은 남긴다
    assert repo.results == [(1, [1], [2, 3])]
    assert repo.notified == [(1, ["20250311000001", "20250311000002"], "push")]


def test_send_user_alerts_push_all_gone_is_retried_later():
    repo = AlertRepo([sub(1, "https://fcm.googleapis.com/gone")])
    run = send_user_alerts(repo, {"push": FakePush(gone={"https://fcm.googleapis.com/gone"})})
    assert run.sent == 0 and repo.notified == [] and "브라우저가 없습니다" in run.errors[0]
    assert repo.results == [(1, [], [1])]
    with pytest.raises(SendError):
        send_push(AlertRepo([]), FakePush(), 1, {"title": "x"})


# --- API ---------------------------------------------------------------------------


def make_app(push=True):
    repo = FakeRepo()
    _, public = webpush.generate_vapid_keys()
    fake = FakePush(public_key=public)

    @contextmanager
    def repo_cm():
        yield repo

    services = Services(
        repo_cm,
        lambda r: FakeAnswerer(),
        lambda r: FakeRetriever(),
        auth_required=True,
        senders=lambda: {"push": fake} if push else {},
        public_url="https://dart.example",
        secret_key="k" * 32,
    )
    client = TestClient(create_app(services, auth.LoginLimiter()))
    assert client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW}).is_success
    return client, repo, fake


def subscription(endpoint=ENDPOINT):
    _, p256dh, auth_secret = browser_keys()
    return {
        "endpoint": endpoint,
        "expirationTime": None,
        "keys": {"p256dh": p256dh, "auth": auth_secret},
    }


def test_push_subscribe_test_and_remove():
    client, repo, fake = make_app()
    info = client.get("/api/alerts").json()
    assert info["available"]["push"] is True and info["push_public_key"] == fake.public_key
    assert info["push_devices"] == []

    chrome = {"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/131.0 Safari/537.36"}
    r = client.post("/api/alerts/push", json=subscription(), headers=chrome)
    assert r.status_code == 201
    second = "https://updates.push.services.mozilla.com/wpush/v2/phone"
    assert client.post("/api/alerts/push", json=subscription(second)).status_code == 201
    info = client.get("/api/alerts").json()
    [push_channel] = [c for c in info["channels"] if c["kind"] == "push"]
    assert push_channel["verified"] and push_channel["enabled"]
    first = info["push_devices"][0]
    assert first["label"] == "Chrome · Windows"
    # 화면에는 주소 대신 주소의 해시만 준다
    assert first["key"] == hashlib.sha256(ENDPOINT.encode()).hexdigest()
    assert ENDPOINT not in json.dumps(info) and "abc123" not in json.dumps(info)
    stored = repo.push_subscriptions(1)[0]
    assert stored["vapid_key"] == fake.public_key

    # 시험 알림: 모든 브라우저로. 끝난 구독(410)은 지운다
    fake.gone.add(second)
    r = client.post("/api/alerts/push/test")
    assert r.json() == {"ok": True, "sent": 1}
    assert fake.sent[0][1]["title"] == "DART 공시 알림 시험"
    assert [s["endpoint"] for s in repo.push_subscriptions(1)] == [ENDPOINT]
    assert client.post("/api/alerts/push/test").status_code == 429  # 1분에 한 번

    # 켜고 끄기는 다른 채널과 같다
    assert client.patch("/api/alerts/push", json={"enabled": False}).is_success
    assert repo.channels[(1, "push")]["enabled"] is False

    # 이 브라우저 해제 → 마지막 구독이면 채널도 사라진다
    r = client.request("DELETE", "/api/alerts/push/device", json={"endpoint": ENDPOINT})
    assert r.json() == {"ok": True, "removed": True}
    assert repo.push_subscriptions(1) == [] and (1, "push") not in repo.channels

    # 목록의 다른 브라우저 해제, 채널 통째로 지우기
    client.post("/api/alerts/push", json=subscription())
    client.post("/api/alerts/push", json=subscription(second))
    device_id = client.get("/api/alerts").json()["push_devices"][1]["id"]
    assert client.delete(f"/api/alerts/push/devices/{device_id}").is_success
    assert client.delete(f"/api/alerts/push/devices/{device_id}").status_code == 404
    assert client.delete("/api/alerts/push").is_success
    assert repo.push_subscriptions(1) == []


def test_push_rejects_bad_subscriptions_and_other_users():
    client, repo, _ = make_app()
    for body in [
        subscription("http://127.0.0.1:8000/api/health"),
        subscription("https://evil.example/push"),
        subscription() | {"keys": {"p256dh": "AAAA", "auth": "BBBB"}},
        {"endpoint": ENDPOINT},
    ]:
        assert client.post("/api/alerts/push", json=body).status_code == 422
    assert repo.push == {}
    assert client.post("/api/alerts/push", json=subscription()).status_code == 201

    other = TestClient(client.app)
    other.post("/api/auth/signup", json={"email": "c@d.co", "password": PW})
    mine = client.get("/api/alerts").json()["push_devices"][0]["id"]
    assert other.delete(f"/api/alerts/push/devices/{mine}").status_code == 404
    gone = other.request("DELETE", "/api/alerts/push/device", json={"endpoint": ENDPOINT})
    assert gone.json()["removed"] is False and len(repo.push) == 1
    # 같은 브라우저를 다른 사람이 구독하면 그 사람 것으로 옮긴다 (공용 컴퓨터)
    assert other.post("/api/alerts/push", json=subscription()).status_code == 201
    assert repo.push_subscriptions(1) == [] and len(repo.push_subscriptions(2)) == 1
    assert (1, "push") not in repo.channels


def test_push_not_configured():
    client, *_ = make_app(push=False)
    info = client.get("/api/alerts").json()
    assert info["available"]["push"] is False and info["push_public_key"] is None
    assert client.post("/api/alerts/push", json=subscription()).status_code == 503
    assert client.post("/api/alerts/push/test").status_code == 503


# --- 키 만들기 명령, 점검 -----------------------------------------------------------


def test_push_keys_command_writes_env_without_showing_secret(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    r = CliRunner().invoke(cli.app, ["push", "keys", "--env-file", str(env)])
    assert r.exit_code == 0, r.output
    text = env.read_text()
    private = text.split("VAPID_PRIVATE_KEY=")[1].split()[0]
    public = text.split("VAPID_PUBLIC_KEY=")[1].split()[0]
    assert private not in r.output and public in r.output
    assert stat.S_IMODE(env.stat().st_mode) == 0o600  # 새 파일은 본인만 읽게
    assert WebPushSender(private, public, "mailto:a@b.co")

    # 이미 있으면 바꾸지 않는다 (바꾸면 모든 구독이 끊긴다)
    again = CliRunner().invoke(cli.app, ["push", "keys", "--env-file", str(env)])
    assert again.exit_code == 1 and "--force" in again.output and env.read_text() == text
    forced = CliRunner().invoke(cli.app, ["push", "keys", "--env-file", str(env), "--force"])
    assert forced.exit_code == 0 and env.read_text() != text
    assert env.read_text().count("VAPID_PRIVATE_KEY=") == 1

    # .env.example 처럼 빈 줄이 있으면 그 자리를 채운다
    example = tmp_path / "example.env"
    example.write_text("SECRET_KEY=x\nVAPID_PUBLIC_KEY=\nVAPID_PRIVATE_KEY=\nVAPID_SUBJECT=\n")
    example.chmod(0o644)
    r = CliRunner().invoke(cli.app, ["push", "keys", "--env-file", str(example)])
    lines = example.read_text().splitlines()
    assert lines[0] == "SECRET_KEY=x" and lines[3] == "VAPID_SUBJECT=" and len(lines) == 4
    assert "chmod 600" in r.output


def test_push_keys_print_mode(monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
    r = CliRunner().invoke(cli.app, ["push", "keys", "--print"])
    assert r.exit_code == 0
    out = r.stdout
    private = out.split("VAPID_PRIVATE_KEY=")[1].split()[0]
    public = out.split("VAPID_PUBLIC_KEY=")[1].split()[0]
    assert WebPushSender(private, public, "x").public_key == public
    # 서버에 이미 키가 있으면 바꾸지 않는다
    monkeypatch.setattr(
        cli, "get_settings", lambda: Settings(_env_file=None, vapid_private_key=private)
    )
    r = CliRunner().invoke(cli.app, ["push", "keys", "--print"])
    assert r.exit_code == 1 and "VAPID_PRIVATE_KEY=" not in r.stdout


def test_push_keys_print_refuses_terminal(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdout", Tty())
    with pytest.raises(cli.typer.Exit):
        cli.push_keys(to_stdout=True)


def test_doctor_checks_push_settings():
    private, public = webpush.generate_vapid_keys()
    other, _ = webpush.generate_vapid_keys()
    base = {"_env_file": None, "auth_required": True, "secret_key": "k" * 32}
    ok = check_alerts(Settings(**base, vapid_private_key=private, vapid_public_key=public))
    assert ok.ok and "웹 푸시" in ok.detail
    half = check_alerts(Settings(**base, vapid_private_key=private))
    assert not half.ok and "모두 필요" in half.detail
    wrong = check_alerts(Settings(**base, vapid_private_key=other, vapid_public_key=public))
    assert not wrong.ok and "짝이 아닙니다" in wrong.detail and other not in wrong.detail
    prod = check_alerts(
        Settings(
            **base,
            vapid_private_key=private,
            vapid_public_key=public,
            environment="production",
            public_url="http://dart.example",
        )
    )
    assert not prod.ok and "mailto:" in prod.detail
    assert check_alerts(
        Settings(
            **base,
            vapid_private_key=private,
            vapid_public_key=public,
            environment="production",
            vapid_subject="mailto:ops@dart.example",
        )
    ).ok
