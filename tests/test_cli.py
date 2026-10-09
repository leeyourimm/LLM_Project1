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
