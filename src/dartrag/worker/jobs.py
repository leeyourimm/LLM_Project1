"""작업 본체. Celery 없이도 부를 수 있게 일반 함수로 둔다 (CLI·테스트에서 직접 실행).

작업마다 job_runs 에 시작·끝·결과를 남기고, 같은 작업이 겹쳐 돌지 않게 Redis 잠금을 건다.
"""

import logging
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, timedelta

from dartrag.config import Settings

log = logging.getLogger(__name__)


class JobSkipped(Exception):
    """같은 작업이 이미 돌고 있어 건너뜀."""


@dataclass
class Context:
    """작업에 필요한 것들. 무거운 것(임베딩 모델 등)은 처음 쓸 때 만든다."""

    settings: Settings
    redis: object = None
    _backends: object = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "Context":
        redis = None
        if settings.redis_url:
            import redis as redis_lib

            redis = redis_lib.Redis.from_url(settings.redis_url)
        return cls(settings, redis)

    @property
    def backends(self):
        if self._backends is None:
            from dartrag.factory import Backends

            self._backends = Backends(self.settings)
        return self._backends

    @contextmanager
    def repo(self):
        from dartrag.db import Repository

        r = Repository.connect(self.settings.database_url)
        try:
            yield r
        finally:
            r.conn.close()

    def quota(self):
        if self.redis is None:
            return None
        from dartrag.dart.quota import DailyQuota

        return DailyQuota(self.redis, self.settings.dart_daily_limit)

    @contextmanager
    def dart(self):
        from dartrag.dart import OpenDartClient
        from dartrag.dart.quota import SharedThrottle

        s = self.settings
        shared = SharedThrottle(self.redis, s.dart_min_interval) if self.redis else None
        with OpenDartClient(
            s.dart_api_key,
            min_interval=s.dart_min_interval,
            quota=self.quota(),
            shared_throttle=shared,
        ) as client:
            yield client

    def store(self):
        from dartrag.storage import make_raw_store

        return make_raw_store(self.settings)


def _plain(value):
    if is_dataclass(value):
        return asdict(value)
    return value


def run_job(ctx: Context, name: str, fn, *, lock_key: str | None = None, lock_seconds=3600):
    """잠금 → 실행 기록 → 실행. 이미 돌고 있으면 JobSkipped."""
    key = f"dartrag:job:{lock_key or name}"
    token = uuid.uuid4().hex
    # 작업자가 죽어도 lock_seconds 가 지나면 풀린다
    if ctx.redis is not None and not ctx.redis.set(key, token, nx=True, ex=lock_seconds):
        raise JobSkipped(name)
    try:
        with ctx.repo() as repo:
            job_id = repo.start_job(name)
            try:
                result = fn(ctx, repo)
            except Exception as e:
                repo.conn.rollback()
                repo.finish_job(job_id, "error", {"error": f"{type(e).__name__}: {e}"})
                raise
            repo.finish_job(job_id, "ok", _plain(result))
            return result
    finally:
        if ctx.redis is not None:
            # 내가 건 잠금일 때만 푼다 (시간이 지나 다른 작업자가 잡았을 수 있다)
            if (ctx.redis.get(key) or b"").decode() == token:
                ctx.redis.delete(key)


# --- 작업들 ------------------------------------------------------------------


def feed_poll(ctx: Context, repo, days: int = 1):
    """최근 공시 목록을 받아 분류·저장 (정기공시는 처리 대기열에 들어간다)."""
    from dartrag.pipeline.feed import poll

    today = date.today()
    with ctx.dart() as client:
        s = poll(client, repo, today - timedelta(days=max(days - 1, 0)), today)
    return {"fetched": s.fetched, "new": s.new}


def ingest(ctx: Context, repo, limit: int = 20):
    """대기열의 정기보고서를 수집한다. 새 데이터가 생긴 회사를 돌려준다."""
    from dartrag.pipeline.ingest import ingest_pending

    with ctx.dart() as client:
        s = ingest_pending(client, repo, ctx.store(), limit=limit)
    return {
        "filings": s.filings,
        "corp_codes": s.corp_codes,
        "errors": s.errors,
        "stopped": s.stopped,
    }


def process(ctx: Context, repo, corp_codes: list[str] | None = None, limit: int | None = None):
    """수집된 원문 파싱 → 색인 → 변경점 요약 → 데이터 검증.

    corp_codes 가 없으면 밀린 것 전체(과거 데이터 채우기 뒤처리)를 limit 건까지."""
    from dartrag.answer import LLMError
    from dartrag.changes.summary import latest_digest
    from dartrag.finance.validate import validate_company
    from dartrag.pipeline.index import index_filings
    from dartrag.pipeline.parse import parse_filings

    store = ctx.store()
    parsed = parse_filings(repo, store, corp_codes, limit=limit)
    b = ctx.backends
    indexed = index_filings(repo, b.embedder, b.vector, b.keyword, corp_codes, limit=limit)
    summaries, issues = 0, 0
    if corp_codes:
        from dartrag.factory import build_llm

        llm = build_llm(ctx.settings)
        for code in corp_codes:
            for kind in ("사업보고서", "반기보고서", "분기보고서"):
                try:
                    if latest_digest(repo, code, llm, kind):
                        summaries += 1
                except (LLMError, ValueError) as e:
                    log.warning("변경점 요약 실패 %s %s: %s", code, kind, e)
            issues += len(validate_company(repo, code))
    return {
        "parsed": parsed.filings,
        "indexed": indexed.filings,
        "summaries": summaries,
        "issues": issues,
        "errors": parsed.errors + indexed.errors,
    }


def send_alerts(ctx: Context, repo):
    from dartrag.factory import build_notifiers, build_senders
    from dartrag.feed.alerts import send_user_alerts, unsubscribe_link
    from dartrag.pipeline.feed import send_alerts as send_operator

    s = ctx.settings
    sent, errors = 0, []
    for notifier in build_notifiers(s, log.info):
        n, errs = send_operator(repo, notifier)
        sent, errors = sent + n, errors + errs
    run = send_user_alerts(
        repo, build_senders(s), unsubscribe_url=unsubscribe_link(s.public_url, s.secret_key)
    )
    return {"operator": sent, "users": run.sent, "items": run.items, "errors": errors + run.errors}


def backfill(ctx: Context, repo):
    """전체 상장사 과거 데이터를 하루 호출 한도 안에서 조금씩 채운다."""
    from dartrag.pipeline.backfill import backfill_step
    from dartrag.pipeline.collect import sync_companies

    s = ctx.settings
    with ctx.dart() as client:
        progress = repo.backfill_progress()
        if not any(progress.values()):
            sync_companies(client, repo)
            repo.plan_backfill(s.backfill_start_year, date.today().year)
        result = backfill_step(
            client,
            repo,
            ctx.store(),
            max_companies=s.backfill_batch,
            quota=ctx.quota(),
            reserve=s.dart_reserve,
        )
    return {
        "companies": len(result.companies),
        "filings": result.filings,
        "errors": result.errors,
        "stopped": result.stopped,
        "progress": repo.backfill_progress(),
    }


def validate(ctx: Context, repo):
    from dartrag.finance.validate import run_validation

    return run_validation(repo)


def maintenance(ctx: Context, repo):
    """보관 기간이 지난 대화, 만료된 세션, 오래된 발송·실행 기록 정리."""
    s = ctx.settings
    return {
        "conversations": repo.purge_conversations(s.conversation_retention_days),
        "sessions": repo.purge_expired_sessions(),
        "user_notifications": repo.purge_user_notifications(90),
        "job_runs": repo.purge_job_runs(90),
    }


def evaluate(ctx: Context, repo, limit: int | None = None, eval_dir: str = "eval"):
    """정기 평가: 직접 쓴 문항과 재무 DB 로 새로 만든 숫자 문항 일부로 답변 품질을 잰다.

    결과는 eval_runs 에 쌓여 대시보드의 품질 추이와 배포 기준 판정에 쓰인다."""
    import random
    from pathlib import Path

    from dartrag.answer.prompt import PROMPT_VERSION
    from dartrag.eval import load_cases
    from dartrag.eval.generate import generate_cases, load_values
    from dartrag.eval.service import run_and_record
    from dartrag.factory import build_answerer

    s = ctx.settings
    limit = limit or s.eval_schedule_cases
    manual = load_cases(Path(eval_dir) / "manual.jsonl")
    companies = repo.listed_companies()
    # 주마다 다른 숫자 문항을 뽑아 특정 문항에만 맞춘 개선을 막는다
    week = date.today().isocalendar()
    seed = week.year * 100 + week.week
    generated = generate_cases(
        companies, load_values(repo, [c for c, _ in companies]), per_company=4, seed=seed
    )
    rng = random.Random(seed)
    n_generated = min(len(generated), limit // 2)
    cases = rng.sample(manual, min(len(manual), limit - n_generated)) + rng.sample(
        generated, n_generated
    )
    meta = {
        "llm": s.llm_model,
        "embed": s.embed_model,
        "rerank": s.rerank_model or "-",
        "prompt": PROMPT_VERSION,
        "cases": len(cases),
        "files": f"scheduled (seed {seed})",
    }
    answerer = build_answerer(ctx.backends, repo, use_cache=False, use_fallback=False)
    result = run_and_record(repo, answerer, cases, Path("reports/eval"), meta)
    return {
        "cases": len(cases),
        "pass_rate": result["summary"]["overall"]["pass_rate"],
        "passed": result["passed"],
        "failed_checks": [c.name for c in result["checks"] if not c.ok],
        "report": str(result["report"]),
    }
