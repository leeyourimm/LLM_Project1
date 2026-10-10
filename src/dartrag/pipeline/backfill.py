"""전체 상장사의 과거 정기보고서 채우기.

상장사 2,500여 곳 × 10년이면 OpenDART 호출이 수십만 번이라 하루 한도(키당 20,000회)
안에서 며칠에 걸쳐 나눠 한다. 회사 단위로 진행 상황을 backfill_state 에 남겨서
작업자가 재시작돼도 이어서 하고, 새 공시 처리 몫(reserve)은 항상 남겨 둔다.
INDEX_SCOPE=focus 이면 계획은 전체 상장사로 세우되 자동 색인 대상 회사만 채운다.
"""

import logging
from dataclasses import dataclass, field
from datetime import date

from dartrag.dart.client import QuotaExceeded
from dartrag.pipeline.collect import CollectSummary, _collect_company

log = logging.getLogger(__name__)

DEFAULT_START_YEAR = 2015


@dataclass
class BackfillSummary:
    companies: list[str] = field(default_factory=list)
    filings: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: str | None = None  # quota: 남은 호출 수가 reserve 아래로 내려감


def backfill_step(
    client,
    repo,
    store,
    *,
    max_companies: int = 20,
    quota=None,
    reserve: int = 3000,
    today: date | None = None,
    focus=None,
) -> BackfillSummary:
    """focus: 자동 색인 대상 종목코드 (Settings.index_focus). None 이면 모든 상장사."""
    summary = BackfillSummary()
    today = today or date.today()
    for corp_code, start_year, end_year in repo.next_backfill(max_companies, focus=focus):
        if quota is not None and quota.remaining() < reserve:
            summary.stopped = "quota"
            break
        s = CollectSummary()
        try:
            _collect_company(
                client, repo, store, corp_code, start_year, end_year, True, ("CFS", "OFS"), today, s
            )
        except QuotaExceeded:
            summary.stopped = "quota"
            break
        except Exception as e:  # noqa: BLE001
            log.warning("과거 데이터 채우기 실패 %s: %s", corp_code, e)
            repo.fail_backfill(corp_code, f"{type(e).__name__}: {e}")
            summary.errors.append(f"{corp_code}: {e}")
            continue
        repo.finish_backfill(corp_code, s.filings)
        summary.companies.append(corp_code)
        summary.filings += s.filings
    return summary
