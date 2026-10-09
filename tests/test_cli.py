import logging

from typer.testing import CliRunner

from dartrag import cli
from dartrag.dart import DartApiError


def test_collect_reports_maintenance_without_traceback(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "test-key")
    cli.get_settings.cache_clear()
    monkeypatch.setattr(cli.Repository, "connect", classmethod(lambda cls, url: object()))

    def fail(*args, **kwargs):
        raise DartApiError("800", "시스템 점검")

    monkeypatch.setattr(cli, "collect", fail)
    result = CliRunner().invoke(cli.app, ["collect", "-s", "005930"])

    assert result.exit_code == 1
    assert "시스템 점검 중입니다" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    # 요청 URL(인증키 포함)이 로그에 남지 않아야 한다
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    cli.get_settings.cache_clear()


def test_user_add_hides_and_hashes_password(monkeypatch):
    created = []

    class Repo:
        def migrate(self):
            pass

        def create_user(self, email, password_hash):
            created.append((email, password_hash))
            return None if len(created) > 1 else 1

    monkeypatch.setattr(cli.Repository, "connect", classmethod(lambda cls, url: Repo()))
    pw = "correct horse battery"
    r = CliRunner().invoke(cli.app, ["user", "add", "A@B.co"], input=f"{pw}\n{pw}\n")
    assert r.exit_code == 0 and pw not in r.output
    assert created[0][0] == "a@b.co" and created[0][1].startswith("scrypt$")
    r = CliRunner().invoke(cli.app, ["user", "add", "a@b.co"], input=f"{pw}\n{pw}\n")
    assert r.exit_code == 1 and "이미" in r.output
    r = CliRunner().invoke(cli.app, ["user", "add", "a@b.co"], input="short\nshort\n")
    assert r.exit_code == 1 and "10자" in r.output
    assert CliRunner().invoke(cli.app, ["user", "add", "bad"]).exit_code == 1
