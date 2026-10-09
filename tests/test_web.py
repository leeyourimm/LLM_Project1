import json
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
        self.convs = {}
        self.msg_seq = 0
        self.feedback = {}
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

    COMPANIES = {
        "005930": ("00126380", "삼성전자", "005930"),
        "000660": ("00164779", "SK하이닉스", "000660"),
    }
    FIN = {
        "00126380": {
            "ifrs-full_Revenue": {2023: 258_935_494_000_000, 2024: 300_870_903_000_000},
            "dart_OperatingIncomeLoss": {2023: 6_566_976_000_000, 2024: 32_725_961_000_000},
        },
        "00164779": {
            "ifrs-full_Revenue": {2024: 66_192_960_000_000, 2025: 90_000_000_000_000},
        },
    }

    def company_by_stock(self, stock):
        return self.COMPANIES.get(stock)

    def financial_rows(self, corp_codes, reprt_code, sj_divs, account_ids, account_names):
        from dartrag.finance import FinancialRow

        return [
            FinancialRow(code, y, "CFS", aid, "x", amt, f"rcpt{y}")
            for code in corp_codes
            for aid in account_ids
            for y, amt in self.FIN.get(code, {}).get(aid, {}).items()
        ]

    def quarter_rows(self, corp_code, sj_divs, account_ids, account_names):
        from dartrag.finance import FinancialRow

        if "ifrs-full_Revenue" not in account_ids or corp_code != "00126380":
            return []
        q = {"11013": (70, 70), "11012": (75, 145), "11014": (80, 225), "11011": (300, None)}
        return [
            FinancialRow(
                corp_code,
                2024,
                "CFS",
                "ifrs-full_Revenue",
                "x",
                a * 10**12,
                "r",
                1,
                code,
                cum * 10**12 if cum else None,
            )
            for code, (a, cum) in q.items()
        ]

    def latest_filing_per_period(self, corp_code, kind):
        return ["old", "new"]

    def listed_companies(self):
        return [("00126380", "삼성전자"), ("00164779", "SK하이닉스")]

    # 대화 기록
    def create_conversation(self, user_id, title):
        cid = len(self.convs) + 1
        self.convs[cid] = {"user_id": user_id, "title": title, "messages": []}
        return cid

    def owns_conversation(self, cid, user_id):
        return cid in self.convs and self.convs[cid]["user_id"] == user_id

    def add_message(self, cid, role, content, payload):
        self.msg_seq += 1
        self.convs[cid]["messages"].append(
            {"id": self.msg_seq, "role": role, "content": content, "payload": payload}
        )
        return self.msg_seq

    def conversations(self, user_id):
        return [
            {"id": i, "title": c["title"]} for i, c in self.convs.items() if c["user_id"] == user_id
        ]

    def messages(self, cid):
        return self.convs[cid]["messages"]

    def last_context(self, cid):
        users = [m for m in self.convs[cid]["messages"] if m["role"] == "user"]
        return users[-1]["payload"]["context"] if users else None

    def delete_conversation(self, cid, user_id):
        if not self.owns_conversation(cid, user_id):
            return False
        del self.convs[cid]
        return True

    def set_feedback(self, message_id, user_id, rating, reason, comment):
        for c in self.convs.values():
            for m in c["messages"]:
                if m["id"] == message_id and m["role"] == "assistant" and c["user_id"] == user_id:
                    self.feedback[message_id] = (rating, reason, comment)
                    return True
        return False


class FakeLLM:
    name = "fake-llm"


class FakeAnswerer:
    llm = FakeLLM()

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def stream(self, question, flt):
        self.calls.append((question, flt))
        yield ("sources", [HIT])
        if self.fail:
            raise LLMError("Ollama 에 연결할 수 없습니다.")
        yield ("token", "DS 매출은 ")
        yield ("token", "111조원입니다 [1].")
        yield (
            "done",
            check_citations(Answer(question, "DS 매출은 111조원입니다 [1].", hits=[HIT])),
        )

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


def test_company_quarters(ctx):
    client, *_ = ctx
    r = client.get("/api/company/005930/quarters").json()
    labels = [q["label"] for q in r["quarters"]]
    assert labels == ["2024 1Q", "2024 2Q", "2024 3Q", "2024 4Q"]
    q4 = r["quarters"][-1]
    assert q4["values"]["revenue"] == 75 * 10**12 and q4["derived"] == ["revenue"]
    assert client.get("/api/company/000000/quarters").status_code == 404


def test_compare(ctx):
    client, *_ = ctx
    r = client.get("/api/compare", params={"stocks": ["005930", "000660"]}).json()
    assert r["year"] == 2024
    names = [c["corp_name"] for c in r["companies"]]
    assert names == ["삼성전자", "SK하이닉스"]
    assert r["companies"][1]["point"]["values"]["revenue"] == 66_192_960_000_000
    assert client.get("/api/compare", params={"stocks": ["005930"]}).status_code == 422
    dup = client.get("/api/compare", params={"stocks": ["005930", "005930"]})
    assert dup.status_code == 422
    missing = client.get("/api/compare", params={"stocks": ["005930", "111111"]})
    assert missing.status_code == 404


def test_compare_summary(ctx):
    client, _, answerer = ctx
    r = client.post(
        "/api/compare/summary", json={"stocks": ["005930", "000660"], "topic": "risk"}
    ).json()
    question, flt = answerer.calls[-1]
    assert "삼성전자, SK하이닉스" in question and "위험 요인" in question
    assert flt.corp_codes == ["00126380", "00164779"]
    assert r["sources"][0]["cited"] is True and r["answer"].endswith("[1].")
    bad = client.post("/api/compare/summary", json={"stocks": ["005930", "000660"], "topic": "x"})
    assert bad.status_code == 422


def test_report_pdf(ctx):
    client, _, answerer = ctx
    r = client.get("/api/company/005930/report.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and "filename*=UTF-8''" in r.headers["content-disposition"]
    assert not answerer.calls
    r = client.get("/api/company/005930/report.pdf", params={"llm": True})
    assert r.status_code == 200 and len(answerer.calls) == 2
    assert client.get("/api/company/000000/report.pdf").status_code == 404


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


def sse_events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_conversation_follow_up_and_history(ctx):
    client, repo, answerer = ctx
    first = client.post("/api/ask", json={"question": "삼성전자 2024년 영업이익은?"}).json()
    cid = first["conversation_id"]
    assert first["question"] == "삼성전자 2024년 영업이익은?" and first["model"] == "fake-llm"
    assert answerer.calls[-1][1].corp_codes == ["00126380"]  # 질문 속 회사로 검색을 좁힘

    second = client.post("/api/ask", json={"question": "그럼 전년은?", "conversation_id": cid})
    body = second.json()
    assert body["conversation_id"] == cid and body["question"] == "삼성전자 2023년 영업이익?"
    assert body["inherited"] == ["회사", "주제"]
    assert answerer.calls[-1][0] == "삼성전자 2023년 영업이익?"

    convs = client.get("/api/conversations").json()
    assert convs == [{"id": cid, "title": "삼성전자 2024년 영업이익은?"}]
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[1]["payload"]["sources"][0]["cited"] is True

    assert (
        client.post("/api/ask", json={"question": "x 질문", "conversation_id": 99}).status_code
        == 404
    )
    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_ask_stream(ctx):
    client, *_ = ctx
    r = client.post("/api/ask/stream", json={"question": "삼성전자 DS 매출은?"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    assert [e for e, _ in events] == ["meta", "sources", "token", "token", "done"]
    assert "".join(d["text"] for e, d in events if e == "token") == "DS 매출은 111조원입니다 [1]."
    done = events[-1][1]
    assert done["message_id"] and done["sources"][0]["cited"] is True
    assert "투자 권유" in done["disclaimer"]


def test_ask_stream_llm_error():
    repo = FakeRepo()

    @contextmanager
    def repo_cm():
        yield repo

    client = TestClient(create_app(Services(repo_cm, lambda r: FakeAnswerer(fail=True), None)))
    events = sse_events(client.post("/api/ask/stream", json={"question": "매출은?"}).text)
    assert events[-1][0] == "error" and "Ollama" in events[-1][1]["detail"]


def test_feedback(ctx):
    client, repo, _ = ctx
    r = client.post("/api/ask", json={"question": "삼성전자 매출은?"}).json()
    mid = r["message_id"]
    ok = client.post(
        f"/api/messages/{mid}/feedback",
        json={"rating": -1, "reason": "wrong_number", "comment": "단위가 틀림"},
    )
    assert ok.status_code == 200 and repo.feedback[mid] == (-1, "wrong_number", "단위가 틀림")
    assert client.post(f"/api/messages/{mid}/feedback", json={"rating": 5}).status_code == 422
    bad = client.post(f"/api/messages/{mid}/feedback", json={"rating": 1, "reason": "x"})
    assert bad.status_code == 422
    # 질문 메시지나 없는 메시지에는 평가할 수 없음
    assert client.post(f"/api/messages/{mid - 1}/feedback", json={"rating": 1}).status_code == 404


def test_conversations_are_private(secure):
    client, *_ = secure
    client.post("/api/auth/signup", json={"email": "a@b.co", "password": PW})
    cid = client.post("/api/ask", json={"question": "삼성전자 매출은?"}).json()["conversation_id"]
    other = TestClient(client.app)
    other.post("/api/auth/signup", json={"email": "c@d.co", "password": PW})
    assert other.get(f"/api/conversations/{cid}").status_code == 404
    assert other.get("/api/conversations").json() == []
    r = other.post("/api/ask", json={"question": "그럼 전년은?", "conversation_id": cid})
    assert r.status_code == 404
