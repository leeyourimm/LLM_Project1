"""임베딩 모델.

기본은 bge-m3(다국어, 한국어 성능이 좋은 오픈 모델)를 로컬에서 돌린다.
sentence-transformers 가 무거워서 선택 설치(`pip install -e ".[embed]"`)로 뒀다.
"""

import threading
from typing import Protocol


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _on_mps(model) -> bool:
    device = getattr(model, "device", None)
    return getattr(device, "type", None) == "mps"


class SentenceTransformerEmbedder:
    """모델은 처음 임베딩할 때 불러온다. 첫 실행이면 이때 모델 파일(약 2GB)을 내려받는다.

    만들기만 하고 쓰지 않는 경우(색인한 공시가 없어 바로 안내하는 질문 등)에는
    모델을 불러오지 않는다.
    """

    def __init__(
        self, model_name: str = "BAAI/bge-m3", batch_size: int = 16, device: str | None = None
    ):
        self.name = model_name
        self._device = device or None
        self._batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _loaded(self):
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as e:
                    raise RuntimeError(
                        '임베딩 모델을 쓰려면 먼저 설치하세요: pip install -e ".[embed]"'
                    ) from e
                model = SentenceTransformer(self.name, device=self._device)
                if _on_mps(model):
                    # 맥 GPU: 반정밀도로 메모리를 절반만 쓴다. 메모리가 모자라면 GPU 작업이
                    # 오류 없이 멈춰 버리므로(16GB 맥 + Docker) 여유를 남기는 게 중요하다
                    model.half()
                self._model = model
            return self._model

    @property
    def dim(self) -> int:
        return self._loaded().get_sentence_embedding_dimension()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._loaded()
        vectors = model.encode(
            texts, batch_size=self._batch_size, normalize_embeddings=True, show_progress_bar=False
        )
        if _on_mps(model):
            import torch

            torch.mps.empty_cache()  # 묶음마다 쌓이는 GPU 캐시를 돌려준다
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
