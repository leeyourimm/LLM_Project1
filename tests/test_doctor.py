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


@respx.mock
def test_opendart_statuses():
    route = respx.get("https://opendart.fss.or.kr/api/list.json")
    with httpx.Client() as http:
        route.mock(return_value=httpx.Response(200, json={"status": "013"}))
        assert doctor.check_opendart(S, http).ok
        route.mock(return_value=httpx.Response(200, json={"status": "800"}))
        assert "점검" in doctor.check_opendart(S, http).detail
        route.mock(return_value=httpx.Response(200, json={"status": "010"}))
        c = doctor.check_opendart(S, http)
        assert not c.ok and "인증키" in c.detail
        route.mock(side_effect=httpx.ConnectError("boom secret-key-123"))
        c = doctor.check_opendart(S, http)
        assert not c.ok and "secret-key-123" not in format_checks([c])
    assert not doctor.check_opendart(Settings(_env_file=None), httpx.Client()).ok


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
    assert calls[0] == (["005930"], 2022, 2024, False)
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
