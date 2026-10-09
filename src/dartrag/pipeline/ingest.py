"""새로 올라온 정기보고서를 바로 처리한다 (피드 → 원문·재무 → 청크 → 색인).

피드가 10분마다 정기공시를 받아 disclosures 에 넣으면, 이 단계가 대기열처럼
처리하지 않은 것을 꺼내 처리한다. 실패하면 다음에 다시 시도하고, 5번 실패하면 멈춘다.
"""

import logging
from dataclasses import dataclass, field

from dartrag.dart.client import QuotaExceeded
from dartrag.pipeline.collect import CollectSummary, collect_filing

log = logging.getLogger(__name__)


@dataclass
class IngestSummary:
    filings: list[str] = field(default_factory=list)  # 처리한 접수번호
    corp_codes: list[str] = field(default_factory=list)  # 새 데이터가 생긴 회사
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped: str | None = None  # quota: 오늘 호출 한도를 다 씀


def find_filing(client, d: dict):
    """피드 행 → 공시 목록 API 의 Filing (원문·재무 수집에 필요한 정보)."""
    for f in client.iter_filings(
        d["corp_code"], d["rcept_dt"], d["rcept_dt"], pblntf_ty="A", final_only=False
    ):
        if f.rcept_no == d["rcept_no"]:
            return f
    return None


def ingest_pending(client, repo, store, *, limit: int = 20) -> IngestSummary:
    summary = IngestSummary()
    for d in repo.periodic_to_ingest(limit):
        rcept_no = d["rcept_no"]
        try:
            filing = find_filing(client, d)
            if filing is None:
                raise LookupError("공시 목록에서 찾지 못했습니다 (아직 반영 전일 수 있음)")
            collected = collect_filing(
                client, repo, store, filing, repo.fiscal_end_month(d["corp_code"]), CollectSummary()
            )
        except QuotaExceeded:
            summary.stopped = "quota"
            break
        except Exception as e:  # noqa: BLE001 - 한 건 실패가 나머지를 막지 않게
            log.warning("정기보고서 처리 실패 %s: %s", rcept_no, e)
            repo.mark_ingest_failed(rcept_no, f"{type(e).__name__}: {e}")
            summary.errors.append(f"{rcept_no}: {e}")
            continue
        repo.mark_ingested(rcept_no)
        if not collected:  # 정기보고서 이름이 아닌 정기공시 (예: 사업보고서 첨부 정정 등)
            summary.skipped.append(rcept_no)
            continue
        summary.filings.append(rcept_no)
        if d["corp_code"] not in summary.corp_codes:
            summary.corp_codes.append(d["corp_code"])
    return summary
