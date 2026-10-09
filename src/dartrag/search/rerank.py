"""리랭커: 질문과 문서를 함께 읽고 관련도를 다시 매긴다 (cross-encoder).

벡터·키워드 검색은 빠르지만 질문과 문서를 따로 보고 점수를 매긴다.
리랭커는 후보 수십 개만 다시 읽어서 순서를 바로잡는다.
기본 모델 bge-reranker-v2-m3 는 다국어(한국어 포함) 오픈 모델이다.
"""

import math
from typing import Protocol


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", max_length: int = 512):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise RuntimeError('리랭커를 쓰려면 먼저 설치하세요: pip install -e ".[embed]"') from e
        self.name = model_name
        self._model = CrossEncoder(model_name, max_length=max_length)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        raw = [float(x) for x in self._model.predict([(query, t) for t in texts])]
        # 모델에 따라 로짓으로 나오므로 0~1 로 맞춘다
        if any(x < 0 or x > 1 for x in raw):
            raw = [1 / (1 + math.exp(-x)) for x in raw]
        return raw
