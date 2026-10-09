"""가입 없이 체험하기: 체험 계정 만들기, 쓸 수 있는 기능과 막힌 기능, 가입으로 이어 쓰기,
체험 끝내기, 기한이 지난 체험 계정 정리, 체험 계정 요청 한도."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dartrag.accounts import purge_expired_guests
from dartrag.config import DEFAULT_EXAMPLES, Settings
from dartrag.web import Services, create_app
from dartrag.web.guest import GUEST_BLOCKED
from dartrag.web.services import rate_limits
from tests.test_web import PW, FakeAnswerer, FakeRepo, FakeRetriever


class SpyTracer:
    def __init__(self, fail=False):
        self.forgotten = []
        self.fail = fail

    def forget_user(self, user_id):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.forgotten.append(user_id)


def make(*, allow_guest=True, allow_signup=True, limits=None, examples=None):
    repo = FakeRepo()
    tracer = SpyTracer()

    @contextmanager
    def repo_cm():
        yield repo

    extra = {"examples": examples} if examples is not None else {}
    services = Services(
        repo_cm,
        lambda r: FakeAnswerer(),
        lambda r: FakeRetriever(),
        auth_required=True,
        allow_signup=allow_signup,
        allow_guest=allow_guest,
        guest_hours=24,
        tracer=lambda: tracer,
        limits=limits or {},
        **extra,
    )
    return create_app(services), repo, tracer


@pytest.fixture
def guest_app():
    return make()


def start(app) -> TestClient:
    client = TestClient(app)
    r = client.post("/api/auth/guest")
    assert r.status_code == 201, r.text
    return client


def test_login_page_offers_guest_and_creates_session(guest_app):
    app, repo, _ = guest_app
    anon = TestClient(app)
    me = anon.get("/api/auth/me").json()
    assert me["allow_guest"] is True and me["guest_hours"] == 24 and me["user"] is None
    assert anon.get("/api/companies").status_code == 401

    r = anon.post("/api/auth/guest")
    assert r.status_code == 201
    user = r.json()["user"]
    assert user["guest"] is True and user["email"] is None
    until = datetime.fromisoformat(user["guest_expires_at"])
    assert timedelta(hours=23, minutes=59) < until - datetime.now(UTC) <= timedelta(hours=24)
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    # 세션도 체험 기한에 끝난다 (회원 기본 30일이 아니라)
    max_age = int(cookie.split("Max-Age=")[1].split(";")[0])
    assert 86_000 < max_age <= 86_400
    [uid] = repo.guests
    assert repo.users[uid] == (None, None)  # 이메일·비밀번호 없음

    me = anon.get("/api/auth/me").json()["user"]
    assert me["guest"] is True and me["email"] is None and "email_verified" not in me
    # 이미 로그인한 브라우저에서 다시 눌러도 새 계정을 만들지 않는다
    again = anon.post("/api/auth/guest")
    assert again.status_code == 200 and len(repo.guests) == 1
    assert again.json()["user"]["guest_expires_at"] == user["guest_expires_at"]


def test_guest_uses_read_features(guest_app):
    app, repo, _ = guest_app
    client = start(app)
    assert client.get("/api/companies").status_code == 200
    assert client.get("/api/examples").json() == {"questions": list(DEFAULT_EXAMPLES)}
    asked = client.post("/api/ask", json={"question": "삼성전자 DS 매출은?"})
    assert asked.status_code == 200 and asked.json()["answer"].endswith("[1].")
    cid = asked.json()["conversation_id"]
    follow = {"question": "그럼 전년은?", "conversation_id": cid}
    stream = client.post("/api/ask/stream", json=follow)
    assert stream.status_code == 200 and "event: done" in stream.text
    assert [c["id"] for c in client.get("/api/conversations").json()] == [cid]
    assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 4
    assert client.get("/api/company/005930").status_code == 200
    assert client.get("/api/company/005930/quarters").status_code == 200
    assert client.get("/api/compare", params={"stocks": ["005930", "000660"]}).status_code == 200
    assert client.get("/api/company/005930/report.pdf").status_code == 200
    assert client.get("/api/feed").status_code == 200
    assert client.get("/api/diff", params={"stock": "005930"}).status_code == 200
    # 관심 종목 목록은 쓸 수 있다 (알림은 막힌다)
    assert client.post("/api/watchlist", json={"stock": "005930"}).status_code == 201
    assert len(client.get("/api/watchlist").json()) == 1


def _url(path: str) -> str:
    return path.replace("{kind}", "email").replace("{device_id}", "1")


def test_guest_blocked_list_matches_real_routes(guest_app):
    app, *_ = guest_app
    paths = app.openapi()["paths"]
    routes = {(method.upper(), path) for path, ops in paths.items() for method in ops}
    missing = [key for key in GUEST_BLOCKED if key not in routes]
    assert missing == []  # 경로를 바꾸면 이 목록도 함께 고쳐야 한다


def test_guest_gets_clear_403_for_account_features(guest_app):
    app, repo, _ = guest_app
    client = start(app)
    for method, path in GUEST_BLOCKED:
        body = None if method == "GET" else {}
        r = client.request(method, _url(path), json=body)
        assert r.status_code == 403, (method, path, r.text)
        detail = r.json()["detail"]
        if path == "/api/account":
            assert "체험 끝내기" in detail
        else:
            assert detail.startswith("체험 계정에서는") and "가입하면 쓸 수 있습니다" in detail
    assert "알림" in client.get("/api/alerts").json()["detail"]
    assert "2단계 인증" in client.get("/api/auth/2fa").json()["detail"]
    assert "내려받기" in client.get("/api/account/export").json()["detail"]
    [uid] = repo.guests
    assert uid in repo.users and repo.channels == {}

    # 같은 기능을 회원은 그대로 쓴다
    member = TestClient(app)
    member.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    assert member.get("/api/alerts").status_code == 200
    assert member.get("/api/auth/2fa").status_code == 200
    assert member.get("/api/account/export").status_code == 200


def test_blocked_message_without_signup():
    app, *_ = make(allow_signup=False)
    client = start(app)
    detail = client.get("/api/alerts").json()["detail"]
    assert "가입한 계정으로 로그인하면" in detail and "가입하면" not in detail


def test_guest_signup_keeps_history_and_rotates_session(guest_app):
    app, repo, _ = guest_app
    client = start(app)
    old_cookie = client.cookies.get("dartrag_session")
    [uid] = repo.guests
    cid = client.post("/api/ask", json={"question": "삼성전자 매출은?"}).json()["conversation_id"]
    client.post("/api/watchlist", json={"stock": "005930"})

    # 이미 가입된 이메일이면 체험 계정은 그대로
    other = TestClient(app)
    other.post("/api/auth/signup", json={"email": "taken@b.co", "password": PW})
    dup = client.post("/api/auth/signup", json={"email": "taken@b.co", "password": PW})
    assert dup.status_code == 409 and uid in repo.guests

    r = client.post("/api/auth/signup", json={"email": "Me@B.co", "password": PW})
    assert r.status_code == 201 and r.json()["user"]["email"] == "me@b.co"
    assert uid not in repo.guests and repo.users[uid][0] == "me@b.co"
    me = client.get("/api/auth/me").json()["user"]
    assert me["email"] == "me@b.co" and "guest" not in me
    # 같은 계정이라 대화 기록과 관심 종목이 그대로 남는다
    assert [c["id"] for c in client.get("/api/conversations").json()] == [cid]
    assert len(client.get("/api/watchlist").json()) == 1
    # 체험할 때 쓰던 세션은 끊고 새 세션을 준다
    assert client.cookies.get("dartrag_session") != old_cookie
    stale = TestClient(app, cookies={"dartrag_session": old_cookie})
    assert stale.get("/api/companies").status_code == 401
    # 가입했으니 막혔던 기능을 쓴다
    assert client.get("/api/auth/2fa").status_code == 200
    assert client.post("/api/auth/login", json={"email": "me@b.co", "password": PW}).is_success


def test_guest_logout_deletes_everything(guest_app):
    app, repo, tracer = guest_app
    client = start(app)
    [uid] = repo.guests
    client.post("/api/ask", json={"question": "삼성전자 매출은?"})
    client.post("/api/watchlist", json={"stock": "005930"})
    member = TestClient(app)
    member.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})

    assert client.post("/api/auth/logout").status_code == 200
    assert uid not in repo.users and uid not in repo.guests
    assert all(c["user_id"] != uid for c in repo.convs.values())
    assert all(k[0] != uid for k in repo.watch)
    assert tracer.forgotten == [uid]  # 탈퇴처럼 LLM 추적 삭제도 요청
    assert client.get("/api/companies").status_code == 401
    # 회원 로그아웃은 계정을 지우지 않는다
    member.post("/api/auth/logout")
    assert len(repo.users) == 1 and tracer.forgotten == [uid]


def test_expired_guest_is_logged_out_then_purged(guest_app):
    app, repo, _ = guest_app
    client = start(app)
    keep = start(app)
    old, kept = sorted(repo.guests)
    client.post("/api/ask", json={"question": "삼성전자 매출은?"})
    repo.guests[old] = datetime.now(UTC) - timedelta(seconds=1)
    # 지우기 전이라도 기한이 지나면 로그인이 끊긴다
    assert client.get("/api/companies").status_code == 401
    assert client.get("/api/auth/me").json()["user"] is None
    assert keep.get("/api/companies").status_code == 200

    tracer = SpyTracer()
    assert purge_expired_guests(repo, tracer) == 1
    assert old not in repo.users and kept in repo.users
    assert all(c["user_id"] != old for c in repo.convs.values())
    assert tracer.forgotten == [old]
    assert purge_expired_guests(repo, tracer) == 0


def test_purge_survives_trace_errors(guest_app):
    _, repo, _ = guest_app
    gone = [repo.create_guest(datetime.now(UTC) - timedelta(hours=1)) for _ in range(2)]
    assert purge_expired_guests(repo, SpyTracer(fail=True)) == 2
    assert all(u not in repo.users for u in gone)
    assert purge_expired_guests(repo, None) == 0


def test_guest_mode_off_or_local():
    app, repo, _ = make(allow_guest=False)
    anon = TestClient(app)
    assert anon.get("/api/auth/me").json()["allow_guest"] is False
    r = anon.post("/api/auth/guest")
    assert r.status_code == 403 and "체험" in r.json()["detail"] and repo.users == {}

    repo = FakeRepo()

    @contextmanager
    def repo_cm():
        yield repo

    local = TestClient(create_app(Services(repo_cm, None, None, allow_guest=True)))
    assert local.get("/api/auth/me").json()["allow_guest"] is False
    assert local.post("/api/auth/guest").status_code == 400


def test_guest_creation_is_limited_per_address():
    limits = rate_limits(Settings(_env_file=None, rate_guest_per_hour=2))
    app, repo, _ = make(limits=limits)
    first = TestClient(app)
    made = [c.post("/api/auth/guest").status_code for c in (first, TestClient(app))]
    assert made == [201, 201]
    r = TestClient(app).post("/api/auth/guest")
    assert r.status_code == 429 and "체험 계정 만들기는 1시간에 2번" in r.json()["detail"]
    assert "retry-after" in r.headers and len(repo.guests) == 2
    # 체험 계정으로 로그인한 브라우저도 사용자별이 아니라 접속 주소별로 센다
    # (사용자별이면 새 체험 계정마다 한도가 다시 찬다)
    assert first.post("/api/auth/guest").status_code == 429


def test_guest_ask_limit_is_stricter_and_per_address():
    settings = Settings(
        _env_file=None,
        rate_ask_per_minute=5,
        rate_guest_ask_per_minute=2,
        rate_guest_per_hour=10,
    )
    app, *_ = make(limits=rate_limits(settings))
    guest = start(app)
    codes = [guest.post("/api/ask", json={"question": "매출은?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = guest.post("/api/ask/stream", json={"question": "매출은?"})
    assert r.status_code == 429 and "체험 계정은 1분에 2번" in r.json()["detail"]
    # 체험 계정을 새로 만들어도 같은 접속 주소면 한도가 다시 차지 않는다
    fresh = start(app)
    assert fresh.post("/api/ask", json={"question": "매출은?"}).status_code == 429
    # 회원은 회원 한도대로
    member = TestClient(app)
    member.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    member_codes = [
        member.post("/api/ask", json={"question": "매출은?"}).status_code for _ in range(5)
    ]
    assert member_codes == [200] * 5


def test_guest_heavy_limit():
    settings = Settings(_env_file=None, rate_guest_heavy_per_hour=1)
    app, *_ = make(limits=rate_limits(settings))
    guest = start(app)
    body = {"stocks": ["005930", "000660"], "topic": "risk"}
    assert guest.post("/api/compare/summary", json=body).status_code == 200
    r = guest.post("/api/compare/summary", json=body)
    assert r.status_code == 429 and "체험 계정은 1시간에 1번" in r.json()["detail"]


def test_examples_endpoint_uses_configured_questions():
    app, *_ = make(examples=("삼성전자 배당 정책은?",))
    client = start(app)
    assert client.get("/api/examples").json() == {"questions": ["삼성전자 배당 정책은?"]}
    assert TestClient(app).get("/api/examples").status_code == 401  # 로그인(체험 포함) 뒤에


def test_settings_turn_into_services():
    from dartrag.web.services import default_services

    s = Settings(
        _env_file=None,
        allow_guest=True,
        guest_hours=6,
        example_questions=" 질문 하나 | | 질문 둘",
    )
    services = default_services(s)
    assert services.allow_guest is True and services.guest_hours == 6
    assert services.examples == ("질문 하나", "질문 둘")
    assert Settings(_env_file=None).examples == DEFAULT_EXAMPLES
    assert Settings(_env_file=None, example_questions=" ").examples == DEFAULT_EXAMPLES
    as_json = Settings(_env_file=None, example_questions='["가 | 나 질문", " "]')
    assert as_json.examples == ("가 | 나 질문",)
    limits = rate_limits(Settings(_env_file=None))
    assert [r.limit for r in limits["guest:ask"]] == [2, 20]
    assert [r.limit for r in limits["ask"]] == [6, 200]  # 회원보다 낮다
    assert limits["guest"][0].limit == 5 and limits["guest:heavy"][0].limit == 3
