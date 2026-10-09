"""1단계: 저장된 원문을 파싱해 청크로 저장."""

import logging
from dataclasses import dataclass, field

from dartrag.db import Repository
from dartrag.parsing import chunk_document, parse_document_zip
from dartrag.storage import RawStore

log = logging.getLogger(__name__)

# 파서나 청킹 규칙을 바꾸면 올린다. 이전 버전으로 처리한 공시는 다시 파싱된다.
PARSER_VERSION = 1


@dataclass
class ParseSummary:
    filings: int = 0
    chunks: int = 0
    errors: list[str] = field(default_factory=list)


def parse_filings(
    repo: Repository,
    store: RawStore,
    corp_codes: list[str] | None = None,
    *,
    max_chars: int = 1500,
) -> ParseSummary:
    summary = ParseSummary()
    for rcept_no, corp_code, corp_name, kind, period_key, raw_key in repo.filings_to_parse(
        PARSER_VERSION, corp_codes
    ):
        label = " ".join(p for p in (period_key, kind) if p)
        try:
            docs = parse_document_zip(store.get(raw_key))
        except Exception as e:  # 손상된 zip, 읽을 수 없는 XML
            log.exception("파싱 실패 %s", rcept_no)
            summary.errors.append(f"{rcept_no}: {e}")
            continue
        chunks_by_file = [
            (
                doc.source_file,
                chunk_document(
                    doc,
                    rcept_no=rcept_no,
                    corp_name=corp_name,
                    report_label=label if i == 0 else f"{label} 첨부 {doc.name or ''}".strip(),
                    max_chars=max_chars,
                ),
            )
            for i, doc in enumerate(docs)
        ]
        summary.chunks += repo.replace_chunks(rcept_no, corp_code, chunks_by_file, PARSER_VERSION)
        summary.filings += 1
    return summary
