"""기업 대시보드 캐시와 작업자의 미리 만들기."""

from contextlib import contextmanager
from datetime import date

import fakeredis
import redis
from fastapi.testclient import TestClient

from dartrag.dashboard import DashboardCache, MemoryStore
from dartrag.obs import metrics
from dartrag.web import Services, create_app
from dartrag.worker import jobs
from tests.test_web import FakeAnswerer, FakeRepo, FakeRetriever
from tests.test_worker import make_ctx

SAMSUNG = ("00126380", "삼성전자", "005930")


def count(view: str, result: str) -> float:
    labels = {"view": view, "result": result}
    return metrics.REGISTRY.get_sample_value("dartrag_dashboard_cache_total", labels) or 0


class Repo(FakeRepo):
    """작업자가 쓰는 조회와 분기 실적 호출 수를 더한 가짜 저장소."""

    def __init__(self):
        super().__init__()
        self.quarter_calls = 0

        class Conn:
            def rollback(self):
                pass

        self.conn = Conn()

    def quarter_rows(self, *a):
        self.quarter_calls += 1
        return super().quarter_rows(*a)

    def companies_by_code(self, codes):
        return [c for c in self.COMPANIES.values() if c[0] in codes]

    def watched_companies(self):
        codes = {code for _, code in self.watch}
        return [c for c in self.COMPANIES.values() if c[0] in codes]


def make(cache: DashboardCache | None = None):
    repo = Repo()

    @contextmanager
    def repo_cm():
        yield repo

    services = Services(repo_cm, lambda r: FakeAnswerer(), lambda r: FakeRetriever())
    if cache is not None:
        services.dashboards = cache
    return TestClient(create_app(services)), repo


def test_dashboard_is_reused_until_company_data_changes():
    client, repo = make()
    miss, hit = count("company", "miss"), count("company", "hit")
    first = client.get("/api/company/005930").json()
    built = repo.series_calls
    assert built > 0 and first["series"][-1]["year"] == 2024
    assert first["disclosures"][0]["rcept_dt"] == "2025-03-11"

    again = client.get("/api/company/005930").json()
    assert again == first and repo.series_calls == built
    assert count("company", "miss") == miss + 1 and count("company", "hit") == hit + 1

    # 관심 종목 여부와 고지 문구는 캐시와 관계없이 요청마다 붙는다
    client.post("/api/watchlist", json={"stock": "005930"})
    watched = client.get("/api/company/005930").json()
    assert watched["watched"] is True and "투자 권유" in watched["disclaimer"]
    assert repo.series_calls == built

    # 다른 범위는 따로 저장
    client.get("/api/company/005930", params={"years": 3})
    assert repo.series_calls == built * 2
    # 회사 데이터가 바뀌면(버전이 오르면) 새로 만든다
    repo.versions["00126380"] = 7
    client.get("/api/company/005930")
    assert repo.series_calls == built * 3
    # 다른 회사는 따로
    client.get("/api/company/000660")
    assert repo.series_calls == built * 4


def test_quarters_are_cached_with_the_same_versioning():
    client, repo = make()
    first = client.get("/api/company/005930/quarters").json()
    assert [q["label"] for q in first["quarters"]][-1] == "2024 4Q" and "derived" in first["note"]
    client.get("/api/company/005930/quarters")
    assert repo.quarter_calls == 3  # 매출, 영업이익, 순이익 한 번씩
    repo.versions["00126380"] = 1
    assert client.get("/api/company/005930/quarters").json() == first
    assert repo.quarter_calls == 6


def test_recent_disclosure_window_moves_with_the_day():
    repo, cache = Repo(), DashboardCache()
    cache.company(repo, SAMSUNG, today=date(2026, 10, 9))
    built = repo.series_calls
    cache.company(repo, SAMSUNG, today=date(2026, 10, 9))
    assert repo.series_calls == built
    cache.company(repo, SAMSUNG, today=date(2026, 10, 10))
    assert repo.series_calls == built * 2
    assert repo.feed_args[0] == date(2026, 7, 12)  # 90일 전


def test_memory_store_expires_and_evicts():
    now = [0.0]
    store = MemoryStore(max_items=2, clock=lambda: now[0])
    store.set("a", "1", ex=10)
    store.set("b", "2", ex=10)
    assert store.get("a") == "1"  # a 를 최근에 썼으므로 b 가 먼저 밀린다
    store.set("c", "3", ex=10)
    assert store.get("b") is None and store.get("a") == "1" and store.get("c") == "3"
    now[0] = 11
    assert store.get("a") is None


def test_worker_prebuilds_what_the_api_serves():
    r = fakeredis.FakeRedis()
    repo = Repo()
    ctx = make_ctx(repo, r)
    assert jobs.warm_dashboards(ctx, repo, ["00126380"]) == {
        "companies": 1,
        "built": 1,
        "fresh": 0,
        "errors": [],
    }
    # 바뀐 것이 없으면 다시 만들지 않는다
    assert jobs.warm_dashboards(ctx, repo, ["00126380"])["fresh"] == 1
    assert len(r.keys("dartrag:dashboard:*")) == 2  # 대시보드, 분기 실적

    # API 서버는 같은 Redis 에서 꺼내 쓴다 (직접 계산하지 않음)
    client, api_repo = make(DashboardCache(r))
    hits = count("company", "hit"), count("quarters", "hit")
    body = client.get("/api/company/005930").json()
    client.get("/api/company/005930/quarters")
    assert api_repo.series_calls == 0 and api_repo.quarter_calls == 0
    assert (count("company", "hit"), count("quarters", "hit")) == (hits[0] + 1, hits[1] + 1)
    assert body["corp_name"] == "삼성전자" and body["watched"] is False

    # 주기 실행: 관심 종목 회사 전부, 버전이 오른 회사만 다시
    repo.set_watch("00164779", 2)
    repo.set_watch("00126380", 2)
    repo.versions["00126380"] = 2
    assert jobs.warm_dashboards(ctx, repo) == {
        "companies": 2,
        "built": 2,
        "fresh": 0,
        "errors": [],
    }
    assert jobs.warm_dashboards(ctx, repo)["fresh"] == 2

    # Redis 가 없으면 미리 만들 곳이 없다 (API 서버와 공유할 수 없음)
    assert "skipped" in jobs.warm_dashboards(make_ctx(repo), repo)


def test_one_broken_company_does_not_stop_the_rest():
    class Broken(Repo):
        def data_issues(self, corp_code=None, severity=None):
            if corp_code == "00126380":
                raise RuntimeError("boom")
            return super().data_issues(corp_code, severity)

    repo = Broken()
    ctx = make_ctx(repo, fakeredis.FakeRedis())
    result = jobs.warm_dashboards(ctx, repo, ["00126380", "00164779"])
    assert result["built"] == 1 and "boom" in result["errors"][0]


class DownRedis:
    def get(self, key):
        raise redis.ConnectionError("down")

    def set(self, *a, **k):
        raise redis.ConnectionError("down")


def test_redis_outage_falls_back_to_memory():
    client, repo = make(DashboardCache(DownRedis()))
    errors = count("company", "error")
    first = client.get("/api/company/005930")
    assert first.status_code == 200
    built = repo.series_calls
    assert client.get("/api/company/005930").json() == first.json()
    assert repo.series_calls == built  # 메모리에서 꺼냄
    assert count("company", "error") >= errors + 2


def test_celery_runs_warm_after_processing_and_on_schedule(monkeypatch):
    from dartrag.worker import celery_app

    queued = []
    monkeypatch.setattr(celery_app, "_run", lambda *a, **k: {"parsed": 1})
    monkeypatch.setattr(celery_app.send_alerts, "delay", lambda *a: queued.append("alerts"))
    monkeypatch.setattr(celery_app.warm_dashboards, "delay", lambda *a: queued.append(("warm", *a)))
    monkeypatch.setattr(celery_app.warm_examples, "delay", lambda *a: queued.append("examples"))
    celery_app.process(["00126380"])
    # 색인으로 데이터 버전이 바뀌므로 예시 질문의 답도 다시 넣는다
    assert queued == ["alerts", ("warm", ["00126380"]), "examples"]

    schedule = celery_app.app.conf.beat_schedule["warm-dashboards"]
    assert schedule["task"] == "dartrag.warm_dashboards" and schedule["schedule"] == 900
    assert "dartrag.warm_dashboards" in celery_app.app.tasks
