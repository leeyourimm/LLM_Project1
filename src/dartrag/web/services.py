"""웹 서버가 쓰는 의존성. 테스트에서는 가짜로 바꿔 끼운다.

임베딩 모델·검색 클라이언트는 무거워서 처음 쓸 때 한 번만 만들고,
DB 연결은 요청마다 새로 열어 요청끼리 트랜잭션이 섞이지 않게 한다.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from dartrag.config import Settings
from dartrag.db import Repository


@dataclass
class Services:
    repo: Callable[[], "contextmanager[Repository]"]
    answerer: Callable[[Repository], object]  # Answerer
    retriever: Callable[[Repository], object]  # HybridRetriever


def default_services(settings: Settings) -> Services:
    lock = threading.Lock()
    backends: dict = {}

    def search_backends():
        with lock:
            if not backends:
                from qdrant_client import QdrantClient

                from dartrag.search import KeywordIndex, SentenceTransformerEmbedder, VectorIndex

                backends["embedder"] = SentenceTransformerEmbedder(settings.embed_model)
                backends["vector"] = VectorIndex(QdrantClient(url=settings.qdrant_url))
                backends["keyword"] = KeywordIndex.connect(settings.opensearch_url)
            return backends["embedder"], backends["vector"], backends["keyword"]

    @contextmanager
    def repo() -> Iterator[Repository]:
        r = Repository.connect(settings.database_url)
        try:
            yield r
        finally:
            r.conn.close()

    def retriever(r: Repository):
        from dartrag.search import HybridRetriever

        return HybridRetriever(*search_backends(), r.get_chunks)

    def answerer(r: Repository):
        from dartrag.answer import Answerer, OllamaLLM
        from dartrag.finance import FinanceTool

        llm = OllamaLLM(settings.llm_model, settings.ollama_url)
        return Answerer(retriever(r), llm, finance=FinanceTool(r))

    return Services(repo, answerer, retriever)
