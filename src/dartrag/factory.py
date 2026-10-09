"""검색기·답변기 조립. 웹 서버, CLI, 평가, 워커가 같은 설정으로 만든다."""

import logging
import threading

from dartrag.config import Settings
from dartrag.db import Repository

log = logging.getLogger(__name__)


class Backends:
    """무거운 모델·클라이언트는 처음 쓸 때 한 번만 만든다 (스레드 안전)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._cache: dict = {}

    def _get(self, name: str, make):
        with self._lock:
            if name not in self._cache:
                self._cache[name] = make()
            return self._cache[name]

    @property
    def embedder(self):
        from dartrag.search import SentenceTransformerEmbedder

        return self._get("embedder", lambda: SentenceTransformerEmbedder(self.settings.embed_model))

    @property
    def qdrant(self):
        from qdrant_client import QdrantClient

        return self._get("qdrant", lambda: QdrantClient(url=self.settings.qdrant_url))

    @property
    def vector(self):
        from dartrag.search import VectorIndex

        return self._get("vector", lambda: VectorIndex(self.qdrant))

    @property
    def keyword(self):
        from dartrag.search import KeywordIndex

        return self._get("keyword", lambda: KeywordIndex.connect(self.settings.opensearch_url))

    @property
    def reranker(self):
        if not self.settings.rerank_model:
            return None
        from dartrag.search.rerank import CrossEncoderReranker

        return self._get("reranker", lambda: CrossEncoderReranker(self.settings.rerank_model))

    @property
    def redis(self):
        if not self.settings.redis_url:
            return None
        import redis

        return self._get("redis", lambda: redis.Redis.from_url(self.settings.redis_url))


def build_retriever(backends: Backends, repo: Repository):
    from dartrag.search import HybridRetriever

    s = backends.settings
    return HybridRetriever(
        backends.embedder,
        backends.vector,
        backends.keyword,
        repo.get_chunks,
        reranker=backends.reranker,
        recency_weight=s.recency_weight,
        expand=repo.expand_chunks if s.expand_context else None,
    )


def build_answerer(backends: Backends, repo: Repository, *, use_cache: bool = True):
    from dartrag.answer import Answerer, OllamaLLM
    from dartrag.finance import FinanceTool

    s = backends.settings
    llm = OllamaLLM(s.llm_model, s.ollama_url)
    cache = None
    if use_cache and s.answer_cache and backends.redis is not None:
        from dartrag.answer.cache import AnswerCache

        cache = AnswerCache(
            backends.redis,
            llm.name,
            repo.data_version,
            ttl=s.answer_cache_ttl,
            qdrant=backends.qdrant if s.semantic_cache else None,
            embed=backends.embedder.embed_query if s.semantic_cache else None,
        )
    return Answerer(build_retriever(backends, repo), llm, finance=FinanceTool(repo), cache=cache)
