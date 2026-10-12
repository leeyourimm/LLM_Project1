"""1단계: 저장된 원문을 파싱해 청크로 저장."""

import logging
from dataclasses import dataclass, field, replace

from dartrag.db import Repository
from dartrag.parsing import chunk_document, parse_document_zip
from dartrag.parsing.document import ParsedDocument
from dartrag.storage import RawStore

log = logging.getLogger(__name__)

# 파서나 청킹 규칙을 바꾸면 올린다. 이전 버전으로 처리한 공시는 다시 파싱된다.
PARSER_VERSION = 2  # 2: <LIBRARY> 안의 하위 섹션을 나눠 읽고, 첨부 재무제표 중복을 뺌


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
    limit: int | None = None,
) -> ParseSummary:
    summary = ParseSummary()
    todo = repo.filings_to_parse(PARSER_VERSION, corp_codes)
    for rcept_no, corp_code, corp_name, kind, period_key, raw_key in todo[:limit]:
        label = " ".join(p for p in (period_key, kind) if p)
        try:
            docs = parse_document_zip(store.get(raw_key))
        except Exception as e:  # 손상된 zip, 읽을 수 없는 XML
            log.exception("파싱 실패 %s", rcept_no)
            summary.errors.append(f"{rcept_no}: {e}")
            continue
        if len(docs) > 1 and _has_statements(docs[0]):
            docs = [docs[0]] + [_without_statements(d) for d in docs[1:]]
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


def _squash(text: str) -> str:
    return "".join(text.split())


def _has_statements(doc: ParsedDocument) -> bool:
    """본문에 재무제표 주석이 있는가 ("III. 재무에 관한 사항 > 3. 연결재무제표 주석" 등)."""
    return any(_squash(p).endswith("재무제표주석") for s in doc.sections for p in s.path)


def _without_statements(doc: ParsedDocument) -> ParsedDocument:
    """첨부 감사보고서의 "(첨부)재 무 제 표" 부분을 뺀다. 본문 III 장과 같은 내용이라 두 번 넣으면
    검색 결과 자리를 같은 글이 차지하고 색인 시간만 늘어난다. 감사의견 등 나머지는 남긴다."""
    sections = [s for s in doc.sections if not (s.path and _squash(s.path[0]).startswith("(첨부)"))]
    return replace(doc, sections=sections)
