"""1단계: 청크를 임베딩해 Qdrant(벡터)와 OpenSearch(키워드)에 색인."""

import logging
from dataclasses import dataclass, field

from dartrag.db import Repository
from dartrag.search.embeddings import Embedder
from dartrag.search.keyword import KeywordIndex
from dartrag.search.vector import VectorIndex

log = logging.getLogger(__name__)

# 색인 방식(페이로드, 분석기 설정 등)을 바꾸면 올린다. 모든 공시가 다시 색인된다.
INDEX_VERSION = 1


@dataclass
class IndexSummary:
    filings: int = 0
    chunks: int = 0
    errors: list[str] = field(default_factory=list)


def index_filings(
    repo: Repository,
    embedder: Embedder,
    vector: VectorIndex,
    keyword: KeywordIndex,
    corp_codes: list[str] | None = None,
    *,
    batch_size: int = 64,
    limit: int | None = None,
) -> IndexSummary:
    summary = IndexSummary()
    vector.ensure(embedder.dim)
    keyword.ensure()
    for rcept_no in repo.filings_to_index(INDEX_VERSION, embedder.name, corp_codes)[:limit]:
        chunks = repo.indexed_chunks(rcept_no)
        try:
            # 재파싱으로 청크 ID 가 바뀌었을 수 있으니 공시 단위로 지우고 다시 넣는다
            vector.delete_filing(rcept_no)
            keyword.delete_filing(rcept_no)
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                vector.upsert(batch, embedder.embed_documents([c.text for c in batch]))
                keyword.upsert(batch)
        except Exception as e:
            log.exception("색인 실패 %s", rcept_no)
            summary.errors.append(f"{rcept_no}: {e}")
            continue
        repo.mark_indexed(rcept_no, INDEX_VERSION, embedder.name)
        summary.filings += 1
        summary.chunks += len(chunks)
    keyword.refresh()
    return summary


def rebuild_keyword_index(repo: Repository, keyword: KeywordIndex) -> IndexSummary:
    """이미 색인한 공시를 DB 의 청크로 키워드 색인(OpenSearch)에만 다시 넣는다.

    벡터(Qdrant)는 스냅샷으로 옮기고 키워드 색인은 백업하지 않으므로, 다른 서버로 데이터를
    옮긴 뒤에 쓴다. 임베딩을 다시 계산하지 않아 CPU 만 있는 작은 서버에서도 금방 끝난다.
    """
    summary = IndexSummary()
    keyword.ensure()
    for rcept_no in repo.indexed_filing_numbers():
        chunks = repo.indexed_chunks(rcept_no)
        try:
            keyword.delete_filing(rcept_no)
            keyword.upsert(chunks)
        except Exception as e:
            log.exception("키워드 색인 실패 %s", rcept_no)
            summary.errors.append(f"{rcept_no}: {e}")
            continue
        summary.filings += 1
        summary.chunks += len(chunks)
    keyword.refresh()
    return summary
