from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from dartrag.answer import Answer, LLMError
from dartrag.answer.service import check_citations
from dartrag.search import SearchHit
from dartrag.web import Services, create_app

HIT = SearchHit(
    "c1",
    0.1,
    1,
    2,
    {
        "corp_name": "삼성전자",
        "report_nm": "사업보고서 (2024.12)",
        "section_path": ["II. 사업의 내용"],
        "body": "DS 부문 매출은 111조원이다.",
        "unit": None,
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250311000001",
    },
)


class FakeRepo:
    def __init__(self):
        self.watch = {}
        self.users = {}
        self.sessions = {}
        self.closed = False

    def corp_codes_for_stocks(self, stocks):
        return ["00126380"] if "005930" in stocks else []

    def listed_companies_with_stock(self):
        return [("00126380", "삼성전자", "005930")]

    def recent_disclosures(self, since, min_importance, corp_codes):
        self.feed_args = (since, min_importance, corp_codes)
        return [
            {
                "rcept_no": "20250311000001",
                "corp_name": "삼성전자",
                "report_nm": "주요사항보고서(유상증자결정)",
                "rcept_dt": date(2025, 3, 11),
                "event_label": "유상증자",
                "importance": 3,
                "correction": False,
            }
        ]

    def watchlist(self, user_id=None):
        return [(c, "삼성전자", "005930", m) for (u, c), m in self.watch.items() if u == user_id]

    def set_watch(self, code, imp, user_id=None):
        self.watch[(user_id, code)] = imp

    def remove_watch(self, code, user_id=None):
        return self.watch.pop((user_id, code), None) is not None

    # 로그인
    def create_user(self, email, password_hash):
        if any(e == email for e, _ in self.users.values()):
            return None
        uid = len(self.users) + 1
        self.users[uid] = (email, password_hash)
        return uid

    def user_by_email(self, email):
        return next(((i, e, h) for i, (e, h) in self.users.items() if e == email), None)

    def set_password(self, uid, password_hash):
        self.users[uid] = (self.users[uid][0], password_hash)
        self.sessions = {t: u for t, u in self.sessions.items() if u != uid}

    def create_session(self, token_hash, uid, expires):
        assert expires > datetime.now(UTC)
        self.sessions[token_hash] = uid

    def session_user(self, token_hash):
        uid = self.sessions.get(token_hash)
        return (uid, self.users[uid][0]) if uid else None

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)

    def filing_info(self, rcept_no):
        return {
            "rcept_no": rcept_no,
            "report_nm": rcept_no,
            "corp_name": "삼성전자",
            "parsed": True,
        }

    def chunk_rows(self, rcept_no):
        body = "환율 위험" if rcept_no == "old" else "환율 위험\n관세 위험"
        return [(f"{rcept_no}.xml", 0, ["위험관리"], body)]

    def company_by_stock(self, stock):
        return ("00126380", "삼성전자", "005930") if stock == "005930" else None

    def financial_rows(self, corp_codes, reprt_code, sj_divs, account_ids, account_names):
        from dartrag.finance import FinancialRow

        data = {
            "ifrs-full_Revenue": {2023: 258_935_494_000_000, 2024: 300_870_903_000_000},
            "dart_OperatingIncomeLoss": {2023: 6_566_976_000_000, 2024: 32_725_961_000_000},
        }
        return [
            FinancialRow("00126380", y, "CFS", aid, "x", amt, f"rcpt{y}")
            for aid in account_ids
            for y, amt in data.get(aid, {}).items()
        ]

    def latest_filing_per_period(self, corp_code, kind):
        return ["old", "new"]


class FakeAnswerer:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def answer(self, question, flt):
        self.calls.append((question, flt))
        if self.fail:
            raise LLMError("Ollama 에 연결할 수 없습니다.")
        return check_citations(Answer(question, "DS 매출은 111조원입니다 [1].", hits=[HIT]))


class FakeRetriever:
    def search(self, q, flt, limit):
        return [HIT]


@pytest.fixture
def ctx():
    repo, answerer = FakeRepo(), FakeAnswerer()

    @contextmanager
    def repo_cm():
        yield repo

    app = create_app(Services(repo_cm, lambda r: answerer, lambda r: FakeRetriever()))
    return TestClient(app), repo, answerer


def test_index_and_static(ctx):
    client, *_ = ctx
    page = client.get("/")
    assert page.status_code == 200 and "DART 공시 분석" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json() == {"ok": True}


def test_ask(ctx):
    client, _, answerer = ctx
    r = client.post(
        "/api/ask", json={"question": "DS 매출은?", "stocks": ["005930"], "year_from": 2024}
    )
    body = r.json()
    assert r.status_code == 200
    assert body["answer"] == "DS 매출은 111조원입니다 [1]." and body["warnings"] == []
    assert body["sources"][0]["number"] == 1 and body["sources"][0]["cited"] is True
    assert body["sources"][0]["section"] == "II. 사업의 내용"
    flt = answerer.calls[0][1]
    assert flt.corp_codes == ["00126380"] and flt.year_from == 2024
    assert "투자 권유가 아닙니다" in body["disclaimer"]


def test_ask_errors(ctx):
    client, *_ = ctx
    assert client.post("/api/ask", json={"question": "x"}).status_code == 422
    r = client.post("/api/ask", json={"question": "매출은?", "stocks": ["000000"]})
    assert r.status_code == 404 and "종목코드" in r.json()["detail"]

    @contextmanager
    def repo_cm():
        yield FakeRepo()

    down = TestClient(create_app(Services(repo_cm, lambda r: FakeAnswerer(fail=True), None)))
    r = down.post("/api/ask", json={"question": "매출은?"})
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]


def test_search_companies_feed(ctx):
    client, repo, _ = ctx
    hits = client.get("/api/search", params={"q": "매출", "stocks": "005930"}).json()
    assert hits[0]["dense_rank"] == 1 and hits[0]["keyword_rank"] == 2
    assert client.get("/api/companies").json()[0]["stock_code"] == "005930"
    feed = client.get("/api/feed", params={"days": 7, "min_importance": 3}).json()
    assert feed[0]["url"].endswith("rcpNo=20250311000001") and feed[0]["rcept_dt"] == "2025-03-11"
    assert repo.feed_args[1:] == (3, None)
    client.get("/api/feed", params={"watched_only": True})
    assert repo.feed_args[2] == ["-"]  # 관심 종목이 없으면 빈 결과
    assert client.get("/api/feed", params={"days": 0}).status_code == 422


def test_watchlist_crud(ctx):
    client, *_ = ctx
    assert (
        client.post("/api/watchlist", json={"stock": "005930", "min_importance": 3}).status_code
        == 201
    )
    assert client.get("/api/watchlist").json() == [
        {
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "min_importance": 3,
        }
    ]
    assert client.post("/api/watchlist", json={"stock": "abc"}).status_code == 422
    assert client.delete("/api/watchlist/005930").status_code == 200
    assert client.delete("/api/watchlist/005930").status_code == 404


def test_diff(ctx):
    client, *_ = ctx
    r = client.get("/api/diff", params={"stock": "005930"}).json()
    assert r["title"].startswith("삼성전자 변경점")
    assert r["sections"][0]["key"] == "위험관리" and r["sections"][0]["added"] == ["관세 위험"]
    assert client.get("/api/diff", params={"stock": "000000"}).status_code == 404


def test_company_dashboard(ctx):
    client, repo, _ = ctx
    r = client.get("/api/company/005930").json()
    assert r["corp_name"] == "삼성전자" and r["watched"] is False
    assert [p["year"] for p in r["series"]] == [2023, 2024]
    last = r["series"][-1]
    assert last["values"]["revenue"] == 300_870_903_000_000
    assert last["ratios"]["operating_margin"] == 10.88 and last["ratios"]["debt_ratio"] is None
    assert last["growth"]["revenue"] == 16.2 and last["rcept_no"] == "rcpt2024"
    assert r["disclosures"][0]["url"].endswith("rcpNo=20250311000001")
    assert repo.feed_args[1:] == (1, ["00126380"])
    client.post("/api/watchlist", json={"stock": "005930"})
    assert client.get("/api/company/005930").json()["watched"] is True
    assert client.get("/api/company/000000").status_code == 404
    assert client.get("/api/company/abc").status_code == 422


@pytest.fixture
def secure():
    from dartrag.web.auth import LoginLimiter

    repo = FakeRepo()

    @contextmanager
    def repo_cm():
        yield repo

    services = Services(
        repo_cm, lambda r: FakeAnswerer(), lambda r: FakeRetriever(), auth_required=True
    )
    return TestClient(create_app(services, LoginLimiter(max_failures=3))), repo, services


PW = "correct horse battery"


def test_local_mode_needs_no_login(ctx):
    client, *_ = ctx
    assert client.get("/api/auth/me").json() == {
        "auth_required": False,
        "allow_signup": True,
        "user": None,
    }
    assert client.get("/api/companies").status_code == 200
    r = client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    assert r.status_code == 400


def test_signup_login_logout(secure):
    client, repo, _ = secure
    assert client.get("/api/companies").status_code == 401
    assert client.post("/api/ask", json={"question": "매출은?"}).status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 200

    bad = client.post("/api/auth/signup", json={"email": "nope", "password": PW})
    assert bad.status_code == 422 and "이메일" in bad.json()["detail"]
    short = client.post("/api/auth/signup", json={"email": "a@b.co", "password": "short"})
    assert short.status_code == 422 and "10자" in short.json()["detail"]

    r = client.post("/api/auth/signup", json={"email": " A@B.co ", "password": PW})
    assert r.status_code == 201 and r.json()["user"]["email"] == "a@b.co"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" not in cookie
    assert PW not in str(repo.users) and "scrypt$" in repo.users[1][1]
    # DB 에는 토큰 원문이 아니라 해시만
    assert client.cookies.get("dartrag_session") not in repo.sessions
    assert client.get("/api/auth/me").json()["user"] == {"email": "a@b.co"}
    assert client.get("/api/companies").status_code == 200

    dup = client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    assert dup.status_code == 409

    client.post("/api/auth/logout")
    assert repo.sessions == {}
    assert client.get("/api/companies").status_code == 401

    wrong = client.post("/api/auth/login", json={"email": "a@b.co", "password": "x" * 12})
    assert wrong.status_code == 401
    ghost = client.post("/api/auth/login", json={"email": "z@b.co", "password": PW})
    assert ghost.json()["detail"] == wrong.json()["detail"]  # 가입 여부를 드러내지 않음
    assert client.post("/api/auth/login", json={"email": "A@b.co", "password": PW}).is_success
    assert client.get("/api/companies").status_code == 200


def test_login_rate_limit(secure):
    client, *_ = secure
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    client.post("/api/auth/logout")
    for _ in range(3):
        client.post("/api/auth/login", json={"email": "a@b.co", "password": "wrong-password"})
    r = client.post("/api/auth/login", json={"email": "a@b.co", "password": PW})
    assert r.status_code == 429  # 맞는 비밀번호여도 잠시 막힘


def test_watchlist_is_per_user(secure):
    client, repo, _ = secure
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    client.post("/api/watchlist", json={"stock": "005930"})
    assert len(client.get("/api/watchlist").json()) == 1
    assert client.get("/api/company/005930").json()["watched"] is True

    other = TestClient(client.app)
    other.post("/api/auth/signup", json={"email": "c@d.co", "password": PW})
    assert other.get("/api/watchlist").json() == []
    assert other.get("/api/company/005930").json()["watched"] is False
    assert repo.watchlist(None) == []  # 운영자 알림 목록과는 별개


def test_password_change_and_disabled_signup(secure):
    client, repo, services = secure
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    other = TestClient(client.app)
    other.post("/api/auth/login", json={"email": "a@b.co", "password": PW})
    r = client.post("/api/auth/password", json={"current": "nope-nope-nope", "new": "x" * 12})
    assert r.status_code == 401
    r = client.post("/api/auth/password", json={"current": PW, "new": "new password 123"})
    assert r.status_code == 200
    assert other.get("/api/companies").status_code == 401  # 다른 기기 로그인은 끊김
    assert client.get("/api/companies").status_code == 200

    services.allow_signup = False
    r = TestClient(client.app).post("/api/auth/signup", json={"email": "e@f.co", "password": PW})
    assert r.status_code == 403


def test_security_headers_and_cross_site_block(ctx):
    client, *_ = ctx
    page = client.get("/")
    assert page.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert "content-security-policy" not in client.get("/api/docs").headers
    r = client.post(
        "/api/watchlist", json={"stock": "005930"}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    same = client.post(
        "/api/watchlist", json={"stock": "005930"}, headers={"Origin": "http://testserver"}
    )
    assert same.status_code == 201


def test_password_hashing():
    from dartrag.web import auth

    h = auth.hash_password(PW)
    assert h != auth.hash_password(PW)  # 소금이 달라 매번 다름
    assert auth.verify_password(PW, h) and not auth.verify_password(PW + "!", h)
    assert not auth.verify_password(PW, "garbage") and not auth.verify_password(PW, "md5$x")

    now = [0.0]
    lim = auth.LoginLimiter(max_failures=2, window=60, clock=lambda: now[0])
    lim.failed("1.1.1.1", "a")
    assert not lim.blocked("1.1.1.1", "a")
    lim.failed("1.1.1.1", "a")
    assert lim.blocked("1.1.1.1", "a") and not lim.blocked("2.2.2.2", "b")
    now[0] = 61
    assert not lim.blocked("1.1.1.1", "a")
