"""2단계 인증: TOTP 계산(RFC 4226·6238 테스트 값), 비밀값 암호화, 복구 코드,
등록·로그인·끄기 API."""

import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dartrag import cli
from dartrag.web import Services, auth, create_app, totp
from dartrag.web.ratelimit import Rule
from tests.test_web import PW, FakeAnswerer, FakeRepo, FakeRetriever, FakeSender

# --- RFC 테스트 값 ------------------------------------------------------------------

RFC4226_KEY = b"12345678901234567890"
RFC4226 = [
    "755224",
    "287082",
    "359152",
    "969429",
    "338314",
    "254676",
    "287922",
    "162583",
    "399871",
    "520489",
]


def test_hotp_rfc4226_appendix_d():
    assert [totp.hotp(RFC4226_KEY, i) for i in range(10)] == RFC4226


# RFC 6238 부록 B: (시각, SHA-1, SHA-256, SHA-512), 8자리, 30초
RFC6238 = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]
SEED20 = b"12345678901234567890"
SEED32 = b"12345678901234567890123456789012"
SEED64 = b"1234567890" * 6 + b"1234"


@pytest.mark.parametrize(("at", "sha1", "sha256", "sha512"), RFC6238)
def test_totp_rfc6238_appendix_b(at, sha1, sha256, sha512):
    assert totp.totp(SEED20, at, digits=8) == sha1
    assert totp.totp(SEED32, at, digits=8, algorithm="sha256") == sha256
    assert totp.totp(SEED64, at, digits=8, algorithm="sha512") == sha512


def test_match_step_window_and_replay():
    secret = totp.new_secret()
    assert len(totp.secret_bytes(secret)) == 20
    key = totp.secret_bytes(secret)
    now = 1_700_000_000
    step = totp.time_step(now)
    code = totp.totp(key, now)
    assert totp.match_step(secret, code, now) == step
    assert totp.match_step(secret, f" {code[:3]} {code[3:]} ", now) == step  # 띄어 써도 된다
    # 앞뒤 30초까지는 받고, 그보다 벗어나면 받지 않는다
    assert totp.match_step(secret, totp.totp(key, now - 30), now) == step - 1
    assert totp.match_step(secret, totp.totp(key, now + 30), now) == step + 1
    assert totp.match_step(secret, totp.totp(key, now - 90), now) is None
    # 이미 쓴 구간의 코드는 다시 받지 않는다
    assert totp.match_step(secret, code, now, last_step=step) is None
    assert totp.match_step(secret, totp.totp(key, now + 30), now, last_step=step) == step + 1
    for bad in ["", "12345", "1234567", "abcdef", "12345a"]:
        assert totp.match_step(secret, bad, now) is None


def test_otpauth_uri_and_qr():
    secret = totp.new_secret()
    uri = totp.otpauth_uri(secret, "a@b.co")
    parts = urlsplit(uri)
    assert parts.scheme == "otpauth" and parts.netloc == "totp"
    assert unquote(parts.path) == f"/{totp.ISSUER}:a@b.co"
    q = parse_qs(parts.query)
    assert q["secret"] == [secret] and q["issuer"] == [totp.ISSUER]
    assert q["digits"] == ["6"] and q["period"] == ["30"] and q["algorithm"] == ["SHA1"]
    qr = totp.qr_data_uri(uri)
    assert qr.startswith("data:image/svg+xml") and "script" not in qr.lower()
    assert totp.group_secret("ABCDEFGHIJ") == "ABCD EFGH IJ"


def test_seal_binds_secret_to_key_and_user():
    sealed = totp.seal("k" * 32, 7, "JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in sealed and sealed.startswith("v1:")
    assert totp.unseal("k" * 32, 7, sealed) == "JBSWY3DPEHPK3PXP"
    assert totp.unseal("z" * 32, 7, sealed) is None  # SECRET_KEY 가 바뀜
    assert totp.unseal("k" * 32, 8, sealed) is None  # 다른 사용자 줄로 옮김
    assert totp.unseal("k" * 32, 7, "v1:!!!") is None and totp.unseal("k" * 32, 7, "x") is None
    assert totp.seal("k" * 32, 7, "A") != totp.seal("k" * 32, 7, "A")


def test_recovery_codes():
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(
        re.fullmatch(r"[a-hj-km-np-z2-9]{4}-[a-hj-km-np-z2-9]{4}-[a-hj-km-np-z2-9]{4}", c)
        for c in codes
    )
    c = codes[0]
    h = totp.recovery_hash(1, c)
    assert totp.recovery_hash(1, c.upper().replace("-", " ")) == h  # 대소문자·구분자 무시
    assert totp.recovery_hash(2, c) != h and c not in h


# --- API ---------------------------------------------------------------------------


def make(limits=None):
    repo = FakeRepo()
    mail = FakeSender()

    @contextmanager
    def repo_cm():
        yield repo

    services = Services(
        repo_cm,
        lambda r: FakeAnswerer(),
        lambda r: FakeRetriever(),
        auth_required=True,
        senders=lambda: {"email": mail},
        public_url="https://dart.example",
        secret_key="k" * 32,
        limits=limits or {},
    )
    app = create_app(services, auth.LoginLimiter(max_failures=5))
    return app, repo, mail


def code_for(secret: str, offset: int = 0) -> str:
    return totp.totp(totp.secret_bytes(secret), time.time() + offset)


def enroll(client) -> tuple[str, list[str]]:
    r = client.post("/api/auth/2fa/setup", json={"password": PW})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"].replace(" ", "")
    r = client.post("/api/auth/2fa/enable", json={"code": code_for(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def login(client, password=PW):
    return client.post("/api/auth/login", json={"email": "a@b.co", "password": password})


def test_enroll_requires_password_and_valid_code():
    app, repo, _ = make()
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    other = TestClient(app)
    assert login(other).is_success
    assert client.get("/api/auth/2fa").json() == {"enabled": False, "recovery_codes_left": 0}

    assert client.post("/api/auth/2fa/setup", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/2fa/enable", json={"code": "123456"}).status_code == 400
    r = client.post("/api/auth/2fa/setup", json={"password": PW})
    body = r.json()
    secret = body["secret"].replace(" ", "")
    assert body["account"] == "a@b.co" and f"secret={secret}" in body["otpauth_uri"]
    assert body["qr"].startswith("data:image/svg+xml") and r.headers["cache-control"] == "no-store"
    # 비밀값은 암호화해서 둔다. 첫 코드 확인 전에는 로그인에 쓰지 않는다
    assert secret not in repo.totp_rows[1]["secret"]
    assert not repo.totp_enabled(1) and login(TestClient(app)).json()["user"]

    wrong = client.post("/api/auth/2fa/enable", json={"code": "000000"})
    assert wrong.status_code == 400 and "시계" in wrong.json()["detail"]
    r = client.post("/api/auth/2fa/enable", json={"code": code_for(secret)})
    codes = r.json()["recovery_codes"]
    assert r.status_code == 200 and len(codes) == 10
    assert "dartrag_session=" in r.headers["set-cookie"]  # 이 기기는 새 세션으로
    assert client.get("/api/auth/2fa").json() == {"enabled": True, "recovery_codes_left": 10}
    # 복구 코드는 해시만 저장하고, 켜면 다른 기기의 로그인은 끊는다
    assert not set(codes) & repo.recovery[1]
    assert other.get("/api/companies").status_code == 401
    assert client.get("/api/companies").status_code == 200
    # 켠 상태에서는 다시 등록할 수 없다 (비밀값이 바뀌지 않게)
    assert client.post("/api/auth/2fa/setup", json={"password": PW}).status_code == 409


def test_login_requires_second_step():
    app, repo, _ = make()
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    secret, codes = enroll(client)

    anon = TestClient(app)
    r = login(anon)
    assert r.json() == {"two_factor": True}
    cookie = r.headers["set-cookie"]
    assert "dartrag_session" not in cookie and "dartrag_2fa=" in cookie
    assert "HttpOnly" in cookie and "Path=/api/auth" in cookie and "SameSite=strict" in cookie
    assert anon.get("/api/companies").status_code == 401  # 아직 로그인 전
    assert anon.get("/api/auth/me").json()["user"] is None
    # 쿠키에는 토큰 원문, 저장소에는 해시만
    token = anon.cookies.get("dartrag_2fa")
    assert auth.token_hash(token) in repo.challenges and token not in repo.challenges

    wrong = anon.post("/api/auth/login/2fa", json={"code": "000000"})
    assert wrong.status_code == 401 and wrong.json() == {
        "detail": "인증 코드가 맞지 않습니다",
        "restart": False,
    }
    # 인증 앱 코드 (다음 30초 구간 코드도 받는다; 등록할 때 이번 구간 코드를 이미 썼다)
    ok = anon.post("/api/auth/login/2fa", json={"code": code_for(secret, 30)})
    assert ok.status_code == 200 and ok.json()["user"] == {"email": "a@b.co"}
    assert ok.json()["method"] == "totp"
    assert "dartrag_session=" in ok.headers["set-cookie"]
    assert anon.get("/api/companies").status_code == 200
    assert repo.challenges == {}  # 쓴 단계는 지운다

    # 같은 코드는 다시 쓸 수 없다
    again = TestClient(app)
    login(again)
    assert again.post("/api/auth/login/2fa", json={"code": code_for(secret, 30)}).status_code == 401

    # 복구 코드는 한 번만
    rc = TestClient(app)
    login(rc)
    r = rc.post("/api/auth/login/2fa", json={"code": codes[0].upper()})
    assert r.status_code == 200 and r.json()["method"] == "recovery"
    assert r.json()["recovery_codes_left"] == 9
    reuse = TestClient(app)
    login(reuse)
    assert reuse.post("/api/auth/login/2fa", json={"code": codes[0]}).status_code == 401


def test_second_step_limits():
    app, repo, _ = make()
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    secret, _ = enroll(client)

    # 쿠키가 없거나 만료된 단계
    anon = TestClient(app)
    r = anon.post("/api/auth/login/2fa", json={"code": "123456"})
    assert r.status_code == 401 and r.json()["restart"] is True
    login(anon)
    for c in repo.challenges.values():
        c[2] = datetime.now(UTC) - timedelta(seconds=1)
    r = anon.post("/api/auth/login/2fa", json={"code": code_for(secret, 30)})
    assert r.status_code == 401 and r.json()["restart"] is True
    assert 'dartrag_2fa=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]

    # 한 단계에서 5번 틀리면 처음부터
    repo.challenges.clear()
    login(anon)
    for _ in range(4):
        assert anon.post("/api/auth/login/2fa", json={"code": "000000"}).json()["restart"] is False
    last = anon.post("/api/auth/login/2fa", json={"code": "000000"})
    assert last.json()["restart"] is True and repo.challenges == {}
    # 계정 기준 로그인 시도 제한에도 센다 (5번 틀림 → 맞는 비밀번호·코드도 막힘)
    assert login(anon).status_code == 429


def test_second_step_uses_auth_rate_limit():
    app, repo, _ = make(limits={"auth": [Rule(3, 3600, "1시간에 3번")]})
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    anon = TestClient(app)
    codes = [anon.post("/api/auth/login/2fa", json={"code": "000000"}) for _ in range(4)]
    # 가입도 같은 한도(접속 주소별 "auth")를 써서 세 번째부터 막힌다
    assert [c.status_code for c in codes] == [401, 401, 429, 429]


def test_disable_and_regenerate_need_password_and_code():
    app, repo, _ = make()
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    secret, codes = enroll(client)

    def body(password=PW, code=None):
        return {"password": password, "code": code or codes[1]}

    assert client.request("DELETE", "/api/auth/2fa", json=body("wrong")).status_code == 401
    r = client.request("DELETE", "/api/auth/2fa", json=body(code="zzzz-zzzz-zzzz"))
    assert r.status_code == 401 and "인증 코드" in r.json()["detail"]
    assert repo.totp_enabled(1)

    r = client.post("/api/auth/2fa/recovery-codes", json=body())
    fresh = r.json()["recovery_codes"]
    assert r.status_code == 200 and len(fresh) == 10 and not set(fresh) & set(codes)
    # 예전 복구 코드는 더 쓸 수 없다
    assert client.request("DELETE", "/api/auth/2fa", json=body(code=codes[2])).status_code == 401
    ok = client.request("DELETE", "/api/auth/2fa", json=body(code=code_for(secret, 30)))
    assert ok.status_code == 200 and not repo.totp_enabled(1) and 1 not in repo.recovery
    assert login(TestClient(app)).json()["user"] == {"email": "a@b.co"}


def test_password_reset_keeps_two_factor():
    app, repo, mail = make()
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    secret, codes = enroll(client)
    mail.sent.clear()

    anon = TestClient(app)
    anon.post("/api/auth/password/forgot", json={"email": "a@b.co"})
    [(_, _, text)] = mail.sent
    token = text.split("token=")[1].split()[0]
    new_pw = "brand new password 42"
    r = anon.post("/api/auth/password/reset", json={"token": token, "password": new_pw})
    # 비밀번호는 바뀌었지만 로그인은 아직: 메일만으로는 2단계 인증을 건너뛸 수 없다
    assert r.status_code == 200 and r.json() == {"two_factor": True}
    assert "dartrag_session" not in r.headers["set-cookie"]
    assert anon.get("/api/companies").status_code == 401
    assert repo.totp_enabled(1)
    assert client.get("/api/companies").status_code == 401  # 다른 기기도 끊김
    r = anon.post("/api/auth/login/2fa", json={"code": codes[0]})
    assert r.status_code == 200 and anon.get("/api/companies").status_code == 200
    assert login(TestClient(app), new_pw).json() == {"two_factor": True}


def test_account_delete_needs_code_when_enabled():
    app, repo, _ = make()
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    _, codes = enroll(client)
    r = client.request("DELETE", "/api/account", json={"password": PW})
    assert r.status_code == 401 and "인증 코드" in r.json()["detail"]
    r = client.request("DELETE", "/api/account", json={"password": PW, "code": codes[0]})
    assert r.status_code == 200
    assert repo.users == {} and repo.totp_rows == {} and repo.recovery == {}


def test_unreadable_secret_falls_back_to_recovery_codes(caplog):
    """SECRET_KEY 가 바뀌면 인증 앱 코드는 안 되지만 복구 코드로는 들어올 수 있다."""
    app, repo, _ = make()
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    secret, codes = enroll(client)
    repo.totp_rows[1]["secret"] = totp.seal("other-key" * 4, 1, secret)
    anon = TestClient(app)
    login(anon)
    assert anon.post("/api/auth/login/2fa", json={"code": code_for(secret, 30)}).status_code == 401
    assert "SECRET_KEY" in caplog.text and secret not in caplog.text
    assert anon.post("/api/auth/login/2fa", json={"code": codes[0]}).status_code == 200


def test_two_factor_needs_login_mode():
    @contextmanager
    def repo_cm():
        yield FakeRepo()

    client = TestClient(create_app(Services(repo_cm, lambda r: FakeAnswerer(), None)))
    assert client.get("/api/auth/2fa").status_code == 400
    assert client.post("/api/auth/login/2fa", json={"code": "123456"}).status_code == 400


def test_cli_two_factor_off(monkeypatch):
    calls = []

    class Repo:
        def user_by_email(self, email):
            return (1, email, "x") if email == "a@b.co" else None

        def disable_totp(self, uid):
            calls.append(("off", uid))
            return len(calls) == 1

        def revoke_sessions(self, uid):
            calls.append(("revoke", uid))

    monkeypatch.setattr(cli.Repository, "connect", classmethod(lambda cls, url: Repo()))
    r = CliRunner().invoke(cli.app, ["user", "2fa-off", "A@B.co"])
    assert r.exit_code == 0 and "껐습니다" in r.output
    assert calls == [("off", 1), ("revoke", 1)]
    r = CliRunner().invoke(cli.app, ["user", "2fa-off", "a@b.co"])
    assert r.exit_code == 0 and "쓰지 않습니다" in r.output
    assert CliRunner().invoke(cli.app, ["user", "2fa-off", "x@y.co"]).exit_code == 1
