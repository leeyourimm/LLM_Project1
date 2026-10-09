"""4단계: 새 공시를 받아 분류·저장하고 관심 종목 알림 보내기."""

import logging
from dataclasses import dataclass, field
from datetime import date

from dartrag.db import Repository
from dartrag.feed.events import classify
from dartrag.feed.notify import Notifier, format_alert

log = logging.getLogger(__name__)

DEFAULT_TYPES = ("B", "I")  # 주요사항보고, 거래소공시
LISTED = ("Y", "K", "N")


@dataclass
class FeedSummary:
    fetched: int = 0
    new: int = 0
    notified: int = 0
    errors: list[str] = field(default_factory=list)


def poll(
    client,
    repo: Repository,
    start: date,
    end: date,
    *,
    types: tuple[str, ...] = DEFAULT_TYPES,
    listed_only: bool = True,
) -> FeedSummary:
    summary = FeedSummary()
    rows = []
    for ty in types:
        # 정정공시도 알림 대상이라 최종본만 받지 않는다
        for f in client.iter_filings(None, start, end, pblntf_ty=ty, final_only=False):
            summary.fetched += 1
            if listed_only and f.corp_cls not in LISTED:
                continue
            ev = classify(f.report_nm)
            rows.append(
                {
                    **f.model_dump(
                        include={
                            "rcept_no",
                            "corp_code",
                            "corp_name",
                            "corp_cls",
                            "report_nm",
                            "flr_nm",
                            "rcept_dt",
                            "rm",
                        }
                    ),
                    "stock_code": (f.stock_code or "").strip() or None,
                    "pblntf_ty": ty,
                    "event_type": ev.type,
                    "event_label": ev.label,
                    "importance": ev.importance,
                    "correction": ev.correction,
                }
            )
    summary.new = len(repo.insert_disclosures(rows))
    return summary


def send_alerts(repo: Repository, notifier: Notifier) -> tuple[int, list[str]]:
    sent, errors = 0, []
    for d in repo.pending_alerts(notifier.channel):
        try:
            notifier.send(format_alert(d))
        except Exception as e:
            log.warning("알림 실패 %s: %s", d["rcept_no"], e)
            errors.append(f"{d['rcept_no']}: {e}")
            continue
        repo.mark_notified(d["rcept_no"], notifier.channel)
        sent += 1
    return sent, errors
