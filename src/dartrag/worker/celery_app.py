"""Celery 작업자와 주기 실행(beat).

실행 (Redis 필요):
    celery -A dartrag.worker.celery_app worker -Q dart,process,default -c 2
    celery -A dartrag.worker.celery_app beat

대기열:
- dart: OpenDART 를 부르는 작업 (피드, 새 보고서 수집, 과거 데이터). 한 번에 하나씩만 돌린다
- process: 파싱·임베딩·요약처럼 CPU·메모리를 많이 쓰는 작업
- default: 알림, 검증, 정리

새 정기보고서가 공시되면: 피드(10분마다) → 수집(5분마다) → 처리 → 알림 순으로 이어져
30분 안에 검색·요약·알림까지 반영된다.
"""

import logging

from celery import Celery
from celery.schedules import crontab

from dartrag.config import get_settings
from dartrag.worker import jobs

log = logging.getLogger(__name__)

settings = get_settings()
app = Celery("dartrag", broker=settings.redis_url, backend=settings.redis_url)
app.conf.update(
    task_default_queue="default",
    task_routes={
        "dartrag.feed_poll": {"queue": "dart"},
        "dartrag.ingest": {"queue": "dart"},
        "dartrag.backfill": {"queue": "dart"},
        "dartrag.process": {"queue": "process"},
        "dartrag.process_backlog": {"queue": "process"},
    },
    timezone="Asia/Seoul",
    task_acks_late=True,  # 작업자가 죽으면 다른 작업자가 다시 받는다
    worker_prefetch_multiplier=1,
    task_time_limit=3 * 3600,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
)

_ctx: jobs.Context | None = None


def ctx() -> jobs.Context:
    """작업자 프로세스마다 하나 (임베딩 모델을 한 번만 올린다)."""
    global _ctx
    if _ctx is None:
        _ctx = jobs.Context.from_settings(settings)
    return _ctx


def _run(name, fn, lock_key=None, **kwargs):
    try:
        return jobs.run_job(ctx(), name, lambda c, r: fn(c, r, **kwargs), lock_key=lock_key)
    except jobs.JobSkipped:
        log.info("%s 이(가) 이미 실행 중이라 건너뜀", name)
        return {"skipped": True}


@app.task(name="dartrag.feed_poll")
def feed_poll():
    result = _run("feed_poll", jobs.feed_poll)
    # 주요사항 공시 알림은 바로 보내고, 정기보고서는 수집을 바로 시작한다
    send_alerts.delay()
    ingest.delay()
    return result


@app.task(name="dartrag.ingest")
def ingest():
    result = _run("ingest", jobs.ingest)
    if result.get("corp_codes"):
        process.delay(result["corp_codes"])
    return result


@app.task(name="dartrag.process")
def process(corp_codes: list[str]):
    # 회사가 다르면 함께 돌아도 된다
    key = "process:" + ",".join(sorted(corp_codes))[:200]
    result = _run("process", jobs.process, lock_key=key, corp_codes=corp_codes)
    send_alerts.delay()
    return result


@app.task(name="dartrag.process_backlog")
def process_backlog():
    """과거 데이터 채우기로 쌓인 원문을 조금씩 파싱·색인."""
    return _run("process_backlog", jobs.process, limit=200)


@app.task(name="dartrag.send_alerts")
def send_alerts():
    return _run("send_alerts", jobs.send_alerts)


@app.task(name="dartrag.backfill")
def backfill():
    if not settings.backfill_enabled:
        return {"disabled": True}
    return _run("backfill", jobs.backfill)


@app.task(name="dartrag.validate")
def validate():
    return _run("validate", jobs.validate)


@app.task(name="dartrag.maintenance")
def maintenance():
    return _run("maintenance", jobs.maintenance)


def _every(minutes: int) -> float:
    return max(minutes, 1) * 60.0


app.conf.beat_schedule = {
    "feed-poll": {"task": "dartrag.feed_poll", "schedule": _every(settings.feed_poll_minutes)},
    "ingest": {"task": "dartrag.ingest", "schedule": _every(settings.ingest_minutes)},
    "alerts": {"task": "dartrag.send_alerts", "schedule": _every(settings.alerts_minutes)},
    "backfill": {"task": "dartrag.backfill", "schedule": _every(30)},
    "process-backlog": {"task": "dartrag.process_backlog", "schedule": _every(15)},
    # 새벽에: 전체 검증, 보관 기간 정리
    "validate": {"task": "dartrag.validate", "schedule": crontab(hour=4, minute=10)},
    "maintenance": {"task": "dartrag.maintenance", "schedule": crontab(hour=4, minute=40)},
}
