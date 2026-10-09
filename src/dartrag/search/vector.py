"""Qdrant 벡터 인덱스 (dense 검색)."""

import uuid

from qdrant_client import QdrantClient, models

from dartrag.search.types import IndexedChunk, SearchFilter

COLLECTION = "dart_chunks"
_NAMESPACE = uuid.UUID("6f1d2c1e-6a43-4c47-9b1f-4b8f0d6d2a10")


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


class VectorIndex:
    def __init__(self, client: QdrantClient, collection: str = COLLECTION):
        self.client = client
        self.collection = collection

    def ensure(self, dim: int) -> None:
        if self.client.collection_exists(self.collection):
            size = self.client.get_collection(self.collection).config.params.vectors.size
            if size != dim:
                raise ValueError(
                    f"컬렉션 {self.collection} 의 벡터 차원({size})이 임베딩 모델({dim})과 다릅니다"
                )
            return
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        for field, schema in [
            ("corp_code", models.PayloadSchemaType.KEYWORD),
            ("period_year", models.PayloadSchemaType.INTEGER),
            ("report_kind", models.PayloadSchemaType.KEYWORD),
            ("kind", models.PayloadSchemaType.KEYWORD),
        ]:
            self.client.create_payload_index(self.collection, field, schema)

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
            self.collection, query=vector, query_filter=_to_filter(flt), limit=limit
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
