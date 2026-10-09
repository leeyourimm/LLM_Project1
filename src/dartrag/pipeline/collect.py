"""0단계 수집: 기업 목록 → 정기공시 목록 → 원문 → 재무제표."""

import logging
from dataclasses import dataclass, field
from datetime import date

from dartrag.dart import DartApiError, OpenDartClient
from dartrag.dart.models import FinancialRow
from dartrag.dart.reports import bsns_year_for, parse_report_name
from dartrag.db import Repository
from dartrag.normalize import parse_amount
from dartrag.storage import RawStore
from dartrag.storage.raw import document_key

log = logging.getLogger(__name__)


@dataclass
class CollectSummary:
    companies: int = 0
    filings: int = 0
    documents_downloaded: int = 0
    documents_cached: int = 0
    financial_items: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sync_companies(client: OpenDartClient, repo: Repository) -> dict[str, str]:
    """상장사만 저장하고 종목코드 → 고유번호 매핑을 돌려준다."""
    listed = [c for c in client.corp_codes() if c.is_listed]
    repo.upsert_companies(listed)
    return {c.stock_code: c.corp_code for c in listed}


def to_items(rows: list[FinancialRow], fs_div: str) -> list[dict]:
    items = []
    for r in rows:
        items.append(
            {
                "corp_code": r.corp_code,
                "bsns_year": int(r.bsns_year),
                "reprt_code": r.reprt_code,
                "fs_div": fs_div,
                "sj_div": r.sj_div,
                "account_id": r.account_id,
                "account_nm": r.account_nm.strip(),
                "account_detail": r.account_detail,
                "ord": int(r.ord) if r.ord and r.ord.isdigit() else None,
                # 재무제표 API 금액은 원 단위
                "amount": parse_amount(r.thstrm_amount),
                "add_amount": parse_amount(r.thstrm_add_amount),
                "currency": r.currency,
                "raw_amount": r.thstrm_amount,
                "raw_add_amount": r.thstrm_add_amount,
                "rcept_no": r.rcept_no,
            }
        )
    return items


def collect(
    client: OpenDartClient,
    repo: Repository,
    store: RawStore,
    stock_codes: list[str],
    start_year: int,
    end_year: int,
    *,
    download_documents: bool = True,
    fs_divs: tuple[str, ...] = ("CFS", "OFS"),
    today: date | None = None,
) -> CollectSummary:
    summary = CollectSummary()
    corp_by_stock = sync_companies(client, repo)

    for stock_code in stock_codes:
        corp_code = corp_by_stock.get(stock_code)
        if not corp_code:
            summary.errors.append(f"{stock_code}: 상장사 목록에 없음")
            continue
        summary.companies += 1
        try:
            _collect_company(
                client,
                repo,
                store,
                corp_code,
                start_year,
                end_year,
                download_documents,
                fs_divs,
                today or date.today(),
                summary,
            )
        except DartApiError as e:
            log.exception("수집 실패 %s", stock_code)
            summary.errors.append(f"{stock_code}: {e}")
    return summary


def _collect_company(
    client,
    repo,
    store,
    corp_code,
    start_year,
    end_year,
    download_documents,
    fs_divs,
    today,
    summary,
):
    fiscal_end = repo.fiscal_end_month(corp_code)
    # 연말 결산 보고서는 이듬해 3월에 나오므로 조회 기간 끝은 오늘까지
    for filing in client.iter_filings(corp_code, date(start_year, 1, 1), today):
        report = parse_report_name(filing.report_nm)
        if report is None or not start_year <= report.period_year <= end_year:
            continue
        collect_filing(
            client, repo, store, filing, fiscal_end, summary, download_documents, fs_divs
        )


def collect_filing(
    client,
    repo,
    store,
    filing,
    fiscal_end: int,
    summary: CollectSummary,
    download_documents: bool = True,
    fs_divs: tuple[str, ...] = ("CFS", "OFS"),
) -> bool:
    """정기보고서 한 건: 원문 저장, 공시 등록, 재무제표. 정기보고서가 아니면 False."""
    report = parse_report_name(filing.report_nm)
    if report is None:
        return False
    reprt_code = report.reprt_code(fiscal_end)

    raw_key = None
    if download_documents:
        raw_key = document_key(filing.rcept_no)
        if store.exists(raw_key):
            summary.documents_cached += 1
        else:
            store.put(raw_key, client.document(filing.rcept_no))
            summary.documents_downloaded += 1
    repo.upsert_filing(filing, report, reprt_code, raw_key)
    summary.filings += 1

    try:
        bsns_year = bsns_year_for(report, fiscal_end)
    except NotImplementedError:
        summary.skipped.append(f"{filing.corp_name} {report.period_key}: 비12월 결산")
        return True
    for fs_div in fs_divs:
        rows = client.financial_statements(filing.corp_code, bsns_year, reprt_code, fs_div)
        if not rows:
            continue
        summary.financial_items += repo.replace_financials(
            filing.corp_code, bsns_year, reprt_code, fs_div, to_items(rows, fs_div)
        )
    return True
