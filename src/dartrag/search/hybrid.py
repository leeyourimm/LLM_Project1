"""벡터 + 키워드 검색 결과를 RRF(Reciprocal Rank Fusion)로 합친다.

RRF 는 점수 대신 순위만 쓰기 때문에 코사인 유사도와 BM25 처럼
척도가 다른 점수를 정규화 없이 합칠 수 있다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from dartrag.search.embeddings import Embedder
from dartrag.search.types import SearchFilter


def rrf(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


class DenseSearcher(Protocol):
    def search(self, vector: list[float], flt: SearchFilter, limit: int) -> list[str]: ...


class KeywordSearcher(Protocol):
    def search(self, query: str, flt: SearchFilter, limit: int) -> list[str]: ...


@dataclass
class SearchHit:
    chunk_id: str
    score: float
    dense_rank: int | None
    keyword_rank: int | None
    chunk: dict = field(default_factory=dict)  # 본문·출처 (DB 에서 채움)


class HybridRetriever:
    def __init__(
        self,
        embedder: Embedder,
        dense: DenseSearcher,
        keyword: KeywordSearcher,
        load_chunks: Callable[[list[str]], dict[str, dict]] | None = None,
        *,
        candidates: int = 50,
        rrf_k: int = 60,
    ):
        self.embedder = embedder
        self.dense = dense
        self.keyword = keyword
        self.load_chunks = load_chunks
        self.candidates = candidates
        self.rrf_k = rrf_k

    def search(
        self, query: str, flt: SearchFilter | None = None, limit: int = 10
    ) -> list[SearchHit]:
        flt = flt or SearchFilter()
        dense_ids = self.dense.search(self.embedder.embed_query(query), flt, self.candidates)
        keyword_ids = self.keyword.search(query, flt, self.candidates)
        dense_rank = {cid: i for i, cid in enumerate(dense_ids, start=1)}
        keyword_rank = {cid: i for i, cid in enumerate(keyword_ids, start=1)}
        fused = rrf([dense_ids, keyword_ids], k=self.rrf_k)[:limit]
        hits = [
            SearchHit(cid, score, dense_rank.get(cid), keyword_rank.get(cid))
            for cid, score in fused
        ]
        if self.load_chunks and hits:
            chunks = self.load_chunks([h.chunk_id for h in hits])
            # 인덱스에는 있지만 DB 에서 지워진 청크(재파싱 직후 등)는 버린다
            hits = [h for h in hits if h.chunk_id in chunks]
            for h in hits:
                h.chunk = chunks[h.chunk_id]
        return hits
