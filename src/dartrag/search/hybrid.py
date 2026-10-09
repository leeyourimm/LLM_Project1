"""벡터 + 키워드 검색 결과를 RRF(Reciprocal Rank Fusion)로 합친다.

RRF 는 점수 대신 순위만 쓰기 때문에 코사인 유사도와 BM25 처럼
척도가 다른 점수를 정규화 없이 합칠 수 있다.
"""

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from itertools import zip_longest
from typing import Protocol

from dartrag.search.embeddings import Embedder
from dartrag.search.rerank import Reranker
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
    rerank_score: float | None = None
    duplicates: int = 0  # 같은 문단이 다른 연도 보고서에도 있던 횟수


class HybridRetriever:
    """벡터 + 키워드 → RRF → (중복 제거, 리랭킹, 최신성 가중) → 상위 limit 개.

    회사가 둘 이상이면 회사마다 따로 찾아서 섞는다. 한 번에 찾으면 문서가 많은
    회사가 결과를 독차지해서 비교 질문에 한쪽 근거만 남기 때문이다.
    """

    def __init__(
        self,
        embedder: Embedder,
        dense: DenseSearcher,
        keyword: KeywordSearcher,
        load_chunks: Callable[[list[str]], dict[str, dict]] | None = None,
        *,
        candidates: int = 50,
        rrf_k: int = 60,
        reranker: Reranker | None = None,
        rerank_pool: int = 30,
        recency_weight: float = 0.1,
        expand: Callable[[list[str]], dict[str, str]] | None = None,
    ):
        self.embedder = embedder
        self.dense = dense
        self.keyword = keyword
        self.load_chunks = load_chunks
        self.candidates = candidates
        self.rrf_k = rrf_k
        self.reranker = reranker
        self.rerank_pool = rerank_pool
        self.recency_weight = recency_weight
        self.expand = expand

    def search(
        self, query: str, flt: SearchFilter | None = None, limit: int = 10
    ) -> list[SearchHit]:
        flt = flt or SearchFilter()
        vector = self.embedder.embed_query(query)
        if len(flt.corp_codes) > 1:
            per = max(2, math.ceil(limit / len(flt.corp_codes)))
            groups = [
                self._search_one(query, vector, replace(flt, corp_codes=[code]), per)
                for code in flt.corp_codes
            ]
            hits = _interleave(groups)[:limit]
        else:
            hits = self._search_one(query, vector, flt, limit)
        if self.expand and hits:
            wider = self.expand([h.chunk_id for h in hits])
            for h in hits:
                if wider.get(h.chunk_id):
                    h.chunk["context_body"] = wider[h.chunk_id]
        return hits

    def _search_one(self, query: str, vector, flt: SearchFilter, limit: int) -> list[SearchHit]:
        dense_ids = self.dense.search(vector, flt, self.candidates)
        keyword_ids = self.keyword.search(query, flt, self.candidates)
        dense_rank = {cid: i for i, cid in enumerate(dense_ids, start=1)}
        keyword_rank = {cid: i for i, cid in enumerate(keyword_ids, start=1)}
        pool = max(limit, self.rerank_pool) if self.load_chunks else limit
        fused = rrf([dense_ids, keyword_ids], k=self.rrf_k)[:pool]
        hits = [
            SearchHit(cid, score, dense_rank.get(cid), keyword_rank.get(cid))
            for cid, score in fused
        ]
        if not self.load_chunks or not hits:
            return hits[:limit]
        chunks = self.load_chunks([h.chunk_id for h in hits])
        # 인덱스에는 있지만 DB 에서 지워진 청크(재파싱 직후 등)는 버린다
        hits = [h for h in hits if h.chunk_id in chunks]
        for h in hits:
            h.chunk = chunks[h.chunk_id]
        hits = _dedupe(hits)
        if self.reranker and hits:
            scores = self.reranker.score(query, [_rerank_text(h.chunk) for h in hits])
            for h, sc in zip(hits, scores, strict=True):
                h.rerank_score = sc
                h.score = sc
        _apply_recency(hits, self.recency_weight)
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


def _interleave(groups: list[list[SearchHit]]) -> list[SearchHit]:
    out: list[SearchHit] = []
    seen: set[str] = set()
    for row in zip_longest(*groups):
        for h in row:
            if h is not None and h.chunk_id not in seen:
                seen.add(h.chunk_id)
                out.append(h)
    return out


def _norm_body(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip()


def _dedupe(hits: list[SearchHit]) -> list[SearchHit]:
    """해마다 똑같이 반복되는 문단은 가장 최근 보고서 하나만 남긴다 (점수는 그룹 최고점)."""
    groups: dict[str, list[SearchHit]] = {}
    for h in hits:
        groups.setdefault(_norm_body(h.chunk.get("body", "")), []).append(h)
    out = []
    for group in groups.values():
        newest = max(group, key=lambda h: (h.chunk.get("period_key") or "", h.score))
        newest.score = max(h.score for h in group)
        newest.duplicates = len(group) - 1
        out.append(newest)
    return out


def _rerank_text(chunk: dict) -> str:
    path = " > ".join(chunk.get("section_path", []))
    return (
        f"{chunk.get('corp_name', '')} {chunk.get('report_nm', '')} {path}\n{chunk.get('body', '')}"
    )


def _year(chunk: dict) -> int | None:
    key = chunk.get("period_key")
    return int(key[:4]) if key and key[:4].isdigit() else None


def _apply_recency(hits: list[SearchHit], weight: float) -> None:
    """같은 관련도면 최신 보고서가 앞에 오도록 점수에 조금 더한다 (최대 weight 비율)."""
    years = [y for h in hits if (y := _year(h.chunk)) is not None]
    if not weight or not years or min(years) == max(years):
        return
    lo, hi = min(years), max(years)
    for h in hits:
        y = _year(h.chunk)
        if y is not None:
            h.score *= 1 + weight * (y - lo) / (hi - lo)
