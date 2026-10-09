"""임베딩 모델.

기본은 bge-m3(다국어, 한국어 성능이 좋은 오픈 모델)를 로컬에서 돌린다.
sentence-transformers 가 무거워서 선택 설치(`pip install -e ".[embed]"`)로 뒀다.
"""

from typing import Protocol


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 16):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                '임베딩 모델을 쓰려면 먼저 설치하세요: pip install -e ".[embed]"'
            ) from e
        self.name = model_name
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()
        self._batch_size = batch_size

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, batch_size=self._batch_size, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
