import httpx
import respx

from dartrag import cli, doctor
from dartrag.config import Settings
from dartrag.doctor import Check, all_ok, format_checks

S = Settings(_env_file=None, dart_api_key="secret-key-123")


def test_env_file(tmp_path):
    env = tmp_path / ".env"
    assert doctor.check_env_file(S, env).fix == "cp .env.example .env"
    env.write_text("DART_API_KEY=\n")
    assert "비어" in doctor.check_env_file(Settings(_env_file=None), env).detail
    assert doctor.check_env_file(S, env).ok


API = "https://opendart.fss.or.kr/api"
LIST_OK = {"status": "000", "list": [{"rcept_no": "20260814000123", "report_nm": "반기보고서"}]}


def _dart_routes(list_json=LIST_OK, corp=b"PK\x03\x04zip", fs=None, doc=b"PK\x03\x04zip"):
    def route(path, **response):
        return respx.get(f"{API}/{path}").mock(return_value=httpx.Response(200, **response))

    return {
        "list": route("list.json", json=list_json),
        "corp": route("corpCode.xml", content=corp),
        "fs": route("fnlttSinglAcntAll.json", json=fs or {"status": "000", "list": []}),
        "doc": route("document.xml", content=doc),
    }


@respx.mock
def test_opendart_statuses():
    with httpx.Client() as http:
        routes = _dart_routes()
        c = doctor.check_opendart(S, http)
        assert c.ok
        # 원문은 공시 검색에서 찾은 접수번호로 확인한다
        assert routes["doc"].calls.last.request.url.params["rcept_no"] == "20260814000123"
        assert routes["corp"].called and routes["fs"].called

        _dart_routes(list_json={"status": "013"})
        assert doctor.check_opendart(S, http).ok  # 데이터 없음도 정상 응답

        _dart_routes(list_json={"status": "800", "message": "점검"})
        c = doctor.check_opendart(S, http)
        assert not c.ok and "공시 검색" in c.detail and "점검" in c.detail

        _dart_routes(list_json={"status": "010"})
        c = doctor.check_opendart(S, http)
        assert not c.ok and "인증키" in c.detail

        route = respx.get(f"{API}/list.json")
        route.mock(side_effect=httpx.ConnectError("boom secret-key-123"))
        c = doctor.check_opendart(S, http)
        assert not c.ok and "secret-key-123" not in format_checks([c])
    assert not doctor.check_opendart(Settings(_env_file=None), httpx.Client()).ok


@respx.mock
def test_opendart_maintenance_beyond_search():
    """점검 중에 공시 검색만 열려 있어도 수집에 쓰는 다른 기능이 막히면 ❌ 로 알린다."""
    xml_800 = "<result><status>800</status><message>시스템 점검</message></result>".encode()
    with httpx.Client() as http:
        _dart_routes(corp=xml_800)
        c = doctor.check_opendart(S, http)
        assert not c.ok and "회사 목록" in c.detail and "점검" in c.detail
        assert "다시 실행" in c.fix

        _dart_routes(fs={"status": "800", "message": "점검"})
        c = doctor.check_opendart(S, http)
        assert not c.ok and "재무제표" in c.detail

        _dart_routes(doc=xml_800)
        c = doctor.check_opendart(S, http)
        assert not c.ok and "원문" in c.detail
        assert "secret-key-123" not in format_checks([c])


def test_dart_status_from_head():
    assert doctor._dart_status(b"PK\x03\x04") is None
    assert doctor._dart_status(b'{"status":"000","message":"ok"}') == "000"
    assert doctor._dart_status(b'{"status" : "013"}') == "013"
    assert doctor._dart_status(b"<result><status>800</status></result>") == "800"
    assert doctor._dart_status(b"<html>maintenance</html>") == "900"


@respx.mock
def test_services():
    with httpx.Client() as http:
        respx.get("http://localhost:6333/collections").mock(return_value=httpx.Response(200))
        assert doctor.check_qdrant(S, http).ok
        plugins = respx.get("http://localhost:9200/_cat/plugins")
        plugins.mock(return_value=httpx.Response(200, text="node analysis-nori 2.19"))
        assert doctor.check_opensearch(S, http).ok
        plugins.mock(return_value=httpx.Response(200, text=""))
        assert "nori" in doctor.check_opensearch(S, http).detail
        tags = respx.get("http://localhost:11434/api/tags")
        tags.mock(return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]}))
        assert doctor.check_ollama(S, http).ok
        tags.mock(return_value=httpx.Response(200, json={"models": []}))
        assert doctor.check_ollama(S, http).fix == "ollama pull qwen3:8b"
        tags.mock(side_effect=httpx.ConnectError("x"))
        assert doctor.check_ollama(S, http).fix == "Ollama 앱을 실행하세요"
        respx.get("http://localhost:6333/collections").mock(side_effect=httpx.ConnectError("x"))
        assert doctor.check_qdrant(S, http).fix == "make up-search"


def test_postgres_embed_disk():
    def down(url):
        raise OSError("refused")

    assert doctor.check_postgres(S, down).fix == "make up"
    assert not doctor.check_embedding_package(lambda name: None).ok

    class Usage:
        free = 5 * 1024**3

    c = doctor.check_disk(usage=lambda p: Usage)
    assert not c.ok and not c.required
    assert all_ok([c, Check("x", True)])  # 저장 공간 경고만으로는 막지 않는다
    assert not all_ok([Check("x", False)])


def test_run_stops_when_not_ready_and_summarizes(monkeypatch):
    from typer.testing import CliRunner

    ready = [Check("x", True)]
    monkeypatch.setattr(
        doctor, "run_checks", lambda *a, **k: [Check("Postgres", False, "", "make up")]
    )
    r = CliRunner().invoke(cli.app, ["run"])
    assert r.exit_code == 1 and "make up" in r.output

    calls = []

    def ok(*a, **k):
        calls.append(a)

    def bad(*a, **k):
        calls.append(a)
        raise cli.typer.Exit(1)

    monkeypatch.setattr(doctor, "run_checks", lambda *a, **k: ready)
    for name in ("collect_cmd", "parse", "index", "feed_poll", "eval_generate"):
        monkeypatch.setattr(cli, name, ok)
    monkeypatch.setattr(cli, "eval_run", bad)
    r = CliRunner().invoke(cli.app, ["run", "-s", "005930", "--eval-limit", "5"])
    assert r.exit_code == 1
    assert "✅ 3. 색인" in r.output and "❌ 6. 평가" in r.output
    assert calls[0] == (["005930"], *cli.year_range(None, None), False)
    assert calls[-1][2] == 5
    calls.clear()
    r = CliRunner().invoke(cli.app, ["run", "--eval-limit", "0"])
    assert r.exit_code == 0 and len(calls) == 4


def test_redis_check():
    class Ok:
        def ping(self):
            return True

    def down(url, **kw):
        raise ConnectionError

    assert doctor.check_redis(S, lambda url, **kw: Ok()).ok
    c = doctor.check_redis(S, down)
    assert not c.ok and not c.required and c.fix == "make up"


def test_check_alerts():
    from dartrag.config import Settings
    from dartrag.doctor import check_alerts

    ok = check_alerts(Settings(_env_file=None))
    assert ok.ok and not ok.required and "터미널" in ok.detail
    bad = check_alerts(
        Settings(
            _env_file=None,
            auth_required=True,
            telegram_bot_token="t",
            alert_email_to="me@x.co",
        )
    )
    assert not bad.ok
    assert "SMTP_HOST" in bad.detail and "SECRET_KEY" in bad.detail
    assert "TELEGRAM_BOT_USERNAME" in bad.detail


def test_security_problems_only_for_public_servers():
    assert doctor.security_problems(Settings(_env_file=None)) == []
    problems = doctor.security_problems(
        Settings(
            _env_file=None,
            auth_required=True,
            public_url="https://dart.example",
            cookie_secure=False,
            secret_key="short",
            telegram_webhook_secret="abc",
        )
    )
    text = " ".join(problems)
    assert "COOKIE_SECURE" in text and "SECRET_KEY" in text and "METRICS_TOKEN" in text
    assert "TELEGRAM_WEBHOOK_SECRET" in text
    good = Settings(
        _env_file=None,
        auth_required=True,
        public_url="https://dart.example",
        cookie_secure=True,
        secret_key="x" * 40,
        metrics_token="m" * 32,
    )
    assert doctor.security_problems(good) == [] and doctor.check_security(good).ok
    open_prod = doctor.security_problems(
        Settings(_env_file=None, environment="production", auth_required=False)
    )
    assert any("AUTH_REQUIRED" in p for p in open_prod)


@respx.mock
def test_llm_fallback_check():
    tags = respx.get("http://localhost:11434/api/tags")
    with httpx.Client() as http:
        c = doctor.check_llm_fallback(S, http)
        assert c.ok and not c.required and "사용 안 함" in c.detail
        backup = Settings(_env_file=None, llm_fallback_model="qwen3:1.7b")
        tags.mock(return_value=httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]}))
        c = doctor.check_llm_fallback(backup, http)
        assert not c.ok and not c.required and c.fix == "ollama pull qwen3:1.7b"
        tags.mock(return_value=httpx.Response(200, json={"models": [{"name": "qwen3:1.7b"}]}))
        c = doctor.check_llm_fallback(backup, http)
        assert c.ok and "30초" in c.detail
        same = Settings(_env_file=None, llm_fallback_model="qwen3:8b")
        assert "같아" in doctor.check_llm_fallback(same, http).detail
        tags.mock(side_effect=httpx.ConnectError("x"))
        c = doctor.check_llm_fallback(backup, http)
        assert not c.ok and not c.required
    # 대체 모델이 없거나 받지 않았어도 준비 완료로 본다
    assert all_ok([Check("Ollama 대체 모델", False, required=False)])


def test_year_range_defaults_to_recent_years():
    from datetime import date

    assert cli.year_range(None, None, date(2026, 10, 9)) == (2023, 2026)
    assert cli.year_range(2020, None, date(2026, 10, 9)) == (2020, 2026)
    assert cli.year_range(None, 2024, date(2026, 10, 9)) == (2021, 2024)
    assert cli.year_range(2022, 2024) == (2022, 2024)


def test_run_skips_later_steps_when_collection_stops(monkeypatch):
    from typer.testing import CliRunner

    calls = []
    monkeypatch.setattr(doctor, "run_checks", lambda *a, **k: [Check("x", True)])

    def stopped(*a, **k):
        calls.append("collect")
        raise cli.typer.Exit(cli.COLLECT_STOPPED)

    monkeypatch.setattr(cli, "collect_cmd", stopped)
    for name in ("parse", "index", "feed_poll", "eval_generate", "eval_run"):
        monkeypatch.setattr(cli, name, lambda *a, n=name, **k: calls.append(n))
    r = CliRunner().invoke(cli.app, ["run"])
    assert r.exit_code == 1 and calls == ["collect"]
    assert "건너뜁니다" in r.output and "❌ 1. 수집" in r.output
    assert "2. 파싱" not in r.output
