from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import fakeredis
import pytest

from dartrag.config import Settings
from dartrag.dart.client import QuotaExceeded
from dartrag.dart.quota import DailyQuota, SharedThrottle
from dartrag.pipeline.backfill import backfill_step
from dartrag.pipeline.ingest import ingest_pending
from dartrag.worker import jobs


def test_daily_quota_resets_by_kst_day():
    r = fakeredis.FakeRedis()
    now = [datetime(2026, 10, 12, 23, 59, tzinfo=ZoneInfo("Asia/Seoul"))]
    q = DailyQuota(r, 3, now=lambda: now[0])
    q.use()
    q.use(2)
    assert q.used() == 3 and q.remaining() == 0
    with pytest.raises(QuotaExceeded):
        q.use()
    assert 0 < r.ttl("dartrag:dart-quota:20261012") <= 2 * 86400
    now[0] = datetime(2026, 10, 13, 0, 1, tzinfo=ZoneInfo("Asia/Seoul"))
    assert q.remaining() == 3


def test_shared_throttle_waits_for_slot():
    r = fakeredis.FakeRedis()
    slept = []
    t = SharedThrottle(r, 0.2, sleep=lambda s: (slept.append(s), r.delete(t.key)))
    t.wait()
    assert slept == []
    t.wait()  # 앞 호출의 자리가 남아 있어 한 번 기다린다
    assert len(slept) == 1 and 0 < slept[0] <= 0.2


def test_client_counts_quota_before_each_call():
    import httpx
    import respx

    from dartrag.dart import OpenDartClient

    used = []

    class Q:
        def use(self):
            used.append(1)
            if len(used) > 1:
                raise QuotaExceeded(len(used), 1)

    with respx.mock:
        respx.get("https://opendart.fss.or.kr/api/company.json").mock(
            return_value=httpx.Response(200, json={"status": "000"})
        )
        c = OpenDartClient("k", quota=Q(), sleep=lambda s: None)
        assert c._get_json("/company.json", {})["status"] == "000"
        with pytest.raises(QuotaExceeded):
            c._get_json("/company.json", {})


# --- 새 정기보고서 처리 ----------------------------------------------------------


class Filing:
    def __init__(self, rcept_no, name="사업보고서 (2025.12)", corp="00126380"):
        self.rcept_no = rcept_no
        self.report_nm = name
        self.corp_code = corp
        self.corp_name = "삼성전자"


class Client:
    def __init__(self, filings, quota_after=None):
        self.filings = filings
        self.calls = 0
        self.quota_after = quota_after

    def iter_filings(self, corp_code, start, end, pblntf_ty="A", final_only=True):
        self.calls += 1
        if self.quota_after is not None and self.calls > self.quota_after:
            raise QuotaExceeded(self.calls, self.quota_after)
        return iter(self.filings)

    def document(self, rcept_no):
        return b"PK..."

    def financial_statements(self, *a):
        return []


class Store:
    def __init__(self):
        self.keys = set()

    def exists(self, k):
        return k in self.keys

    def put(self, k, v):
        self.keys.add(k)


class IngestRepo:
    def __init__(self, rows):
        self.rows = rows
        self.done, self.failed, self.filings = [], [], []

    def periodic_to_ingest(self, limit):
        return self.rows[:limit]

    def fiscal_end_month(self, code):
        return 12

    def upsert_filing(self, filing, report, reprt_code, raw_key):
        self.filings.append((filing.rcept_no, reprt_code, raw_key))

    def mark_ingested(self, rcept_no):
        self.done.append(rcept_no)

    def mark_ingest_failed(self, rcept_no, error):
        self.failed.append((rcept_no, error))


def row(no, name="사업보고서 (2025.12)"):
    return {
        "rcept_no": no,
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "report_nm": name,
        "rcept_dt": date(2026, 3, 10),
    }


def test_ingest_pending_collects_and_records_failures():
    repo = IngestRepo([row("20260310000001"), row("20260310000002"), row("20260310000003")])
    client = Client([Filing("20260310000001"), Filing("20260310000003", "기타 정기공시")])
    s = ingest_pending(client, repo, Store())
    assert s.filings == ["20260310000001"] and s.corp_codes == ["00126380"]
    assert s.skipped == ["20260310000003"]
    assert repo.done == ["20260310000001", "20260310000003"]
    assert repo.failed[0][0] == "20260310000002" and "찾지 못했습니다" in repo.failed[0][1]
    assert repo.filings == [("20260310000001", "11011", "documents/2026/20260310000001.zip")]


def test_ingest_stops_on_quota_without_counting_failure():
    repo = IngestRepo([row("20260310000001"), row("20260310000002")])
    s = ingest_pending(Client([Filing("20260310000001")], quota_after=1), repo, Store())
    assert s.stopped == "quota" and repo.done == ["20260310000001"] and repo.failed == []


class BackfillRepo(IngestRepo):
    def __init__(self, plan):
        super().__init__([])
        self.plan = plan
        self.finished, self.errored = [], []

    def next_backfill(self, limit):
        return self.plan[:limit]

    def finish_backfill(self, code, filings):
        self.finished.append((code, filings))

    def fail_backfill(self, code, error):
        self.errored.append(code)


class Quota:
    def __init__(self, remaining):
        self.left = remaining

    def remaining(self):
        return self.left


def test_backfill_step_respects_reserve_and_errors():
    plan = [("00126380", 2015, 2026), ("00164779", 2015, 2026)]
    repo = BackfillRepo(plan)
    s = backfill_step(Client([Filing("20240312000001", "사업보고서 (2023.12)")]), repo, Store())
    assert repo.finished == [("00126380", 1), ("00164779", 1)] and s.filings == 2
    repo = BackfillRepo(plan)
    s = backfill_step(Client([]), repo, Store(), quota=Quota(100), reserve=3000)
    assert s.stopped == "quota" and repo.finished == []

    class Broken(Client):
        def iter_filings(self, *a, **k):
            raise RuntimeError("boom")

    repo = BackfillRepo(plan[:1])
    s = backfill_step(Broken([]), repo, Store())
    assert repo.errored == ["00126380"] and "boom" in s.errors[0]


# --- 작업 실행 기록과 잠금 --------------------------------------------------------


class JobRepo:
    def __init__(self):
        self.jobs = {}
        self.purged = []

        class Conn:
            def rollback(self):
                pass

            def close(self):
                pass

        self.conn = Conn()

    def start_job(self, name):
        self.jobs[len(self.jobs) + 1] = [name, "running", None]
        return len(self.jobs)

    def finish_job(self, job_id, status, detail):
        self.jobs[job_id][1:] = [status, detail]

    def purge_conversations(self, days):
        self.purged.append(("conversations", days))
        return 2

    def purge_expired_sessions(self):
        return 1

    def purge_user_notifications(self, days):
        return 0

    def purge_job_runs(self, days):
        return 3


def make_ctx(repo, redis=None):
    ctx = jobs.Context(Settings(_env_file=None, conversation_retention_days=30), redis)

    @contextmanager
    def fake_repo():
        yield repo

    ctx.repo = fake_repo
    return ctx


def test_run_job_records_result_and_errors():
    repo = JobRepo()
    ctx = make_ctx(repo)
    assert jobs.run_job(ctx, "maintenance", jobs.maintenance)["conversations"] == 2
    assert repo.purged == [("conversations", 30)]
    assert repo.jobs[1][:2] == ["maintenance", "ok"]

    def boom(c, r):
        raise ValueError("bad")

    with pytest.raises(ValueError):
        jobs.run_job(ctx, "validate", boom)
    assert repo.jobs[2] == ["validate", "error", {"error": "ValueError: bad"}]


def test_run_job_skips_when_locked():
    r = fakeredis.FakeRedis()
    ctx = make_ctx(JobRepo(), r)
    r.set("dartrag:job:maintenance", "other-worker", ex=60)
    with pytest.raises(jobs.JobSkipped):
        jobs.run_job(ctx, "maintenance", jobs.maintenance)
    assert r.get("dartrag:job:maintenance") == b"other-worker"  # 남의 잠금은 그대로
    r.delete("dartrag:job:maintenance")
    assert jobs.run_job(ctx, "maintenance", jobs.maintenance)["sessions"] == 1
    assert r.get("dartrag:job:maintenance") is None  # 끝나면 푼다
    # 잠금 이름을 따로 주면 같은 작업이라도 대상이 다르면 함께 돈다
    r.set("dartrag:job:process:a", "x", ex=60)
    assert jobs.run_job(ctx, "process", lambda c, rp: {"ok": 1}, lock_key="process:b") == {"ok": 1}


def test_celery_schedule():
    from dartrag.worker.celery_app import app

    schedule = app.conf.beat_schedule
    assert schedule["feed-poll"]["schedule"] == 600 and schedule["ingest"]["schedule"] == 300
    routes = app.conf.task_routes
    assert routes["dartrag.ingest"]["queue"] == "dart"
    assert routes["dartrag.process"]["queue"] == "process"
    assert {"dartrag.feed_poll", "dartrag.maintenance", "dartrag.validate"} <= set(app.tasks)
