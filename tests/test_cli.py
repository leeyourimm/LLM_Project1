import logging

from typer.testing import CliRunner

from dartrag import cli
from dartrag.dart import DartApiError, DartHttpError


def test_collect_reports_maintenance_without_traceback(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "test-key")
    cli.get_settings.cache_clear()
    monkeypatch.setattr(cli.Repository, "connect", classmethod(lambda cls, url: object()))

    def fail(*args, **kwargs):
        raise DartApiError("800", "시스템 점검")

    monkeypatch.setattr(cli, "collect", fail)
    result = CliRunner().invoke(cli.app, ["collect", "-s", "005930"])

    assert result.exit_code == cli.COLLECT_STOPPED  # dartrag run 이 나머지 단계를 건너뛰는 신호
    assert "시스템 점검 중입니다" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    # 요청 URL(인증키 포함)이 로그에 남지 않아야 한다
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    cli.get_settings.cache_clear()


def test_collect_stops_on_connection_failure(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "test-key")
    cli.get_settings.cache_clear()
    monkeypatch.setattr(cli.Repository, "connect", classmethod(lambda cls, url: object()))

    def fail(*args, **kwargs):
        raise DartHttpError("/corpCode.xml", "HTTP 503")

    monkeypatch.setattr(cli, "collect", fail)
    result = CliRunner().invoke(cli.app, ["collect", "-s", "005930"])

    assert result.exit_code == cli.COLLECT_STOPPED
    assert "연결하지 못해" in result.output and "test-key" not in result.output
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


# --- 자동 색인 대상 (dartrag scope) ------------------------------------------------


class ScopeRepo:
    def __init__(self, companies=()):
        self.scope: list[str] = []
        self.companies = {c[2]: c for c in companies}  # 종목코드 → (고유번호, 이름, 종목코드)

    def migrate(self):
        pass

    def add_index_scope(self, stock):
        if stock in self.scope:
            return False
        self.scope.append(stock)
        return True

    def remove_index_scope(self, stock):
        if stock not in self.scope:
            return False
        self.scope.remove(stock)
        return True

    def company_by_stock(self, stock):
        return self.companies.get(stock)

    def index_scope(self):
        from datetime import datetime

        return [
            (s, (self.companies.get(s) or (None, None))[1], datetime(2026, 10, 10))
            for s in self.scope
        ]


LG = ("00401731", "LG전자", "066570")


def scope_env(monkeypatch, repo, scope="focus"):
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setenv("INDEX_SCOPE", scope)
    cli.get_settings.cache_clear()
    monkeypatch.setattr(cli.Repository, "connect", classmethod(lambda cls, url: repo))
    monkeypatch.setattr(cli, "make_raw_store", lambda settings: object())


def test_scope_add_collects_parses_and_indexes_right_away(monkeypatch):
    from dartrag.pipeline import index
    from dartrag.pipeline.collect import CollectSummary
    from dartrag.pipeline.index import IndexSummary
    from dartrag.pipeline.parse import ParseSummary

    repo = ScopeRepo([LG])
    scope_env(monkeypatch, repo)
    calls = {}

    def collect(client, repo, store, stocks, start_year, end_year):
        calls["collect"] = (stocks, (start_year, end_year))
        return CollectSummary(companies=1, filings=3)

    def parse(repo, store, corp_codes):
        calls["parse"] = corp_codes
        return ParseSummary(filings=3, chunks=900)

    def index_filings(repo, embedder, vector, keyword, corp_codes):
        calls["index"] = corp_codes
        return IndexSummary(filings=3, chunks=900)

    monkeypatch.setattr(cli, "collect", collect)
    monkeypatch.setattr(cli, "parse_filings", parse)
    monkeypatch.setattr(cli, "_search_backends", lambda s: (None, None, None))
    monkeypatch.setattr(index, "index_filings", index_filings)

    r = CliRunner().invoke(cli.app, ["scope", "add", "066570"])
    assert r.exit_code == 0, r.output
    assert repo.scope == ["066570"]
    # dartrag collect 의 기본 기간으로 이 회사 하나만 받아 바로 색인한다
    assert calls == {
        "collect": (["066570"], cli.year_range(None, None)),
        "parse": ["00401731"],
        "index": ["00401731"],
    }
    assert "LG전자: 정기보고서 3건 수집" in r.output and "3건 색인" in r.output

    # 기본 15개사는 따로 넣지 않는다. 잘못된 종목코드는 넣지 않는다
    calls.clear()
    r = CliRunner().invoke(cli.app, ["scope", "add", "005930"])
    assert r.exit_code == 0 and "기본 대상" in r.output and calls == {}
    assert CliRunner().invoke(cli.app, ["scope", "add", "12345"]).exit_code == 1
    assert repo.scope == ["066570"]
    cli.get_settings.cache_clear()


def test_scope_add_keeps_company_when_opendart_fails(monkeypatch):
    for error, text in (
        (DartHttpError("/corpCode.xml", "HTTP 503"), "연결하지 못해"),
        (DartApiError("020", "요청 제한 초과"), "OpenDART 오류"),
    ):
        repo = ScopeRepo([LG])
        scope_env(monkeypatch, repo)

        def fail(*args, error=error, **kwargs):
            raise error

        monkeypatch.setattr(cli, "collect", fail)
        r = CliRunner().invoke(cli.app, ["scope", "add", "066570"])
        assert r.exit_code == 1 and text in r.output and "test-key" not in r.output
        # 회사는 대상에 남아, 작업자가 새 정기보고서를 받으면 처리한다
        assert repo.scope == ["066570"] and "색인 대상에 남아" in r.output
        assert r.exception is None or isinstance(r.exception, SystemExit)
    cli.get_settings.cache_clear()


def test_scope_add_drops_unknown_stock_code(monkeypatch):
    from dartrag.pipeline.collect import CollectSummary

    repo = ScopeRepo()  # 상장사 목록을 받았는데 그 종목코드가 없음
    scope_env(monkeypatch, repo)
    monkeypatch.setattr(
        cli, "collect", lambda *a, **k: CollectSummary(errors=["999999: 상장사 목록에 없음"])
    )
    r = CliRunner().invoke(cli.app, ["scope", "add", "999999"])
    assert r.exit_code == 1 and "없는 종목코드" in r.output and repo.scope == []
    cli.get_settings.cache_clear()


def test_scope_remove_and_list(monkeypatch):
    repo = ScopeRepo([LG])
    repo.scope = ["066570"]
    scope_env(monkeypatch, repo)
    r = CliRunner().invoke(cli.app, ["scope", "list"])
    assert r.exit_code == 0 and "INDEX_SCOPE=focus" in r.output
    assert "기본 15개사: 005930" in r.output and "066570 LG전자" in r.output

    r = CliRunner().invoke(cli.app, ["scope", "remove", "005930"])
    assert r.exit_code == 1 and "뺄 수 없습니다" in r.output
    r = CliRunner().invoke(cli.app, ["scope", "remove", "066570"])
    # 이미 색인한 보고서는 지우지 않는다고 알려 준다
    assert r.exit_code == 0 and repo.scope == [] and "그대로 남습니다" in r.output
    r = CliRunner().invoke(cli.app, ["scope", "remove", "066570"])
    assert r.exit_code == 0 and "더한 회사가 아닙니다" in r.output
    r = CliRunner().invoke(cli.app, ["scope", "list"])
    assert "더한 회사가 없습니다" in r.output
    cli.get_settings.cache_clear()
