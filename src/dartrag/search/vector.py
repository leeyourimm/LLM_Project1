"""Qdrant 벡터 인덱스 (dense 검색).

공시가 몇 년 쌓여도 Qdrant 메모리가 작게 유지되도록, 원본 벡터(float32, bge-m3 는 4KB)는 디스크에
두고 int8 로 줄인 사본(약 1KB)만 메모리에 고정한다. 검색은 int8 사본으로 후보를 넉넉히 고른 뒤
원본 벡터로 다시 점수를 매겨(rescore) 품질을 거의 그대로 지킨다. 디스크는 후보 수만큼만 읽는다.
"""

import logging
import uuid

from qdrant_client import QdrantClient, models

from dartrag.search.types import IndexedChunk, SearchFilter

log = logging.getLogger(__name__)

COLLECTION = "dart_chunks"
# Qdrant 1.19 에서는 on_disk·always_ram(이제 쓰지 않는 옵션) 대신 memory 로 정한다.
# 원본 벡터: cold = 디스크에 두고 읽은 것만 캐시. int8 사본: pinned = 늘 메모리에 (always_ram=True)
VECTOR_MEMORY = models.Memory.COLD
QUANTIZATION = models.ScalarQuantization(
    scalar=models.ScalarQuantizationConfig(
        type=models.ScalarType.INT8, quantile=0.99, memory=models.Memory.PINNED
    )
)
# int8 사본으로 limit 의 2배를 고르고 원본 벡터로 다시 매겨 limit 개를 돌려준다
SEARCH_PARAMS = models.SearchParams(
    quantization=models.QuantizationSearchParams(rescore=True, oversampling=2.0)
)
_NAMESPACE = uuid.UUID("6f1d2c1e-6a43-4c47-9b1f-4b8f0d6d2a10")


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


class VectorIndex:
    def __init__(self, client: QdrantClient, collection: str = COLLECTION):
        self.client = client
        self.collection = collection

    def ensure(self, dim: int) -> None:
        if self.client.collection_exists(self.collection):
            config = self.client.get_collection(self.collection).config
            size = config.params.vectors.size
            if size != dim:
                raise ValueError(
                    f"컬렉션 {self.collection} 의 벡터 차원({size})이 임베딩 모델({dim})과 다릅니다"
                )
            self._upgrade(config)
            return
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE, memory=VECTOR_MEMORY
            ),
            quantization_config=QUANTIZATION,
        )
        for field, schema in [
            ("corp_code", models.PayloadSchemaType.KEYWORD),
            ("period_year", models.PayloadSchemaType.INTEGER),
            ("report_kind", models.PayloadSchemaType.KEYWORD),
            ("kind", models.PayloadSchemaType.KEYWORD),
        ]:
            self.client.create_payload_index(self.collection, field, schema)

    def _upgrade(self, config) -> None:
        """예전 방식(원본 벡터를 메모리에, 양자화 없음)으로 만든 컬렉션을 지금 방식으로 바꾼다.

        이미 바뀐 컬렉션은 건드리지 않는다. 데이터는 그대로 두고 Qdrant 가 뒤에서 세그먼트를
        다시 만들며 옮기므로, 그동안에도 검색과 색인은 된다."""
        if (
            config.params.vectors.memory == VECTOR_MEMORY
            and config.quantization_config == QUANTIZATION
        ):
            return
        log.info("Qdrant %s: 원본 벡터는 디스크로, int8 사본은 메모리로 옮김", self.collection)
        self.client.update_collection(
            self.collection,
            vectors_config={"": models.VectorParamsDiff(memory=VECTOR_MEMORY)},
            quantization_config=QUANTIZATION,
        )

    def upsert(self, chunks: list[IndexedChunk], vectors: list[list[float]]) -> None:
        self.client.upsert(
            self.collection,
            points=[
                models.PointStruct(id=point_id(c.chunk_id), vector=v, payload=c.payload())
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )

    def delete_filing(self, rcept_no: str) -> None:
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="rcept_no", match=models.MatchValue(value=rcept_no)
                        )
                    ]
                )
            ),
        )

    def search(self, vector: list[float], flt: SearchFilter, limit: int) -> list[str]:
        result = self.client.query_points(
            self.collection,
            query=vector,
            query_filter=_to_filter(flt),
            limit=limit,
            search_params=SEARCH_PARAMS,
        )
        return [p.payload["chunk_id"] for p in result.points]


def _to_filter(flt: SearchFilter) -> models.Filter | None:
    must: list[models.Condition] = []
    if flt.corp_codes:
        must.append(
            models.FieldCondition(key="corp_code", match=models.MatchAny(any=flt.corp_codes))
        )
    if flt.year_from is not None or flt.year_to is not None:
        must.append(
            models.FieldCondition(
                key="period_year", range=models.Range(gte=flt.year_from, lte=flt.year_to)
            )
        )
    if flt.report_kinds:
        must.append(
            models.FieldCondition(key="report_kind", match=models.MatchAny(any=flt.report_kinds))
        )
    return models.Filter(must=must) if must else None
