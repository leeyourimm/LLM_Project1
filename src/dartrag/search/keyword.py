"""OpenSearch 키워드 인덱스 (BM25 + nori 한국어 형태소 분석).

벡터 검색은 "매출 감소 원인" 같은 의미 질문에 강하고, 키워드 검색은
"HBM3E", "평택 P4" 같은 고유명사·숫자에 강하다. 둘을 하이브리드로 합친다.
REST API 가 단순해서 별도 클라이언트 라이브러리 없이 httpx 로 호출한다.
"""

import json

import httpx

from dartrag.search.types import IndexedChunk, SearchFilter

INDEX = "dart_chunks"

INDEX_BODY = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0},
        "analysis": {
            "tokenizer": {
                "korean": {"type": "nori_tokenizer", "decompound_mode": "mixed"},
            },
            "analyzer": {
                "korean": {
                    "type": "custom",
                    "tokenizer": "korean",
                    # 조사·어미 등 검색에 의미 없는 품사 제거, 한자 독음 변환, 영문 소문자화
                    "filter": ["nori_part_of_speech", "nori_readingform", "lowercase"],
                }
            },
        },
    },
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "rcept_no": {"type": "keyword"},
            "corp_code": {"type": "keyword"},
            "corp_name": {"type": "keyword"},
            "report_kind": {"type": "keyword"},
            "period_key": {"type": "keyword"},
            "period_year": {"type": "integer"},
            "kind": {"type": "keyword"},
            "section_path": {"type": "text", "analyzer": "korean"},
            "text": {"type": "text", "analyzer": "korean"},
        }
    },
}


class OpenSearchError(RuntimeError):
    pass


class KeywordIndex:
    def __init__(self, client: httpx.Client, index: str = INDEX):
        self.client = client
        self.index = index

    @classmethod
    def connect(cls, url: str, index: str = INDEX) -> "KeywordIndex":
        return cls(httpx.Client(base_url=url, timeout=30), index)

    def ensure(self) -> None:
        if self.client.head(f"/{self.index}").status_code == 200:
            return
        self._check(self.client.put(f"/{self.index}", json=INDEX_BODY))

    def upsert(self, chunks: list[IndexedChunk]) -> None:
        if not chunks:
            return
        lines = []
        for c in chunks:
            lines.append(json.dumps({"index": {"_index": self.index, "_id": c.chunk_id}}))
            lines.append(json.dumps({**c.payload(), "text": c.text}, ensure_ascii=False))
        resp = self.client.post(
            "/_bulk",
            content=("\n".join(lines) + "\n").encode(),
            headers={"Content-Type": "application/x-ndjson"},
        )
        body = self._check(resp)
        if body.get("errors"):
            failed = [item["index"]["error"] for item in body["items"] if "error" in item["index"]]
            raise OpenSearchError(f"색인 실패 {len(failed)}건: {failed[0]}")

    def delete_filing(self, rcept_no: str) -> None:
        resp = self.client.post(
            f"/{self.index}/_delete_by_query",
            params={"conflicts": "proceed"},
            json={"query": {"term": {"rcept_no": rcept_no}}},
        )
        self._check(resp)

    def refresh(self) -> None:
        self._check(self.client.post(f"/{self.index}/_refresh"))

    def search(self, query: str, flt: SearchFilter, limit: int) -> list[str]:
        body = {
            "size": limit,
            "_source": ["chunk_id"],
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["text", "section_path^2"],
                            }
                        }
                    ],
                    "filter": _to_filter(flt),
                }
            },
        }
        hits = self._check(self.client.post(f"/{self.index}/_search", json=body))["hits"]["hits"]
        return [h["_source"]["chunk_id"] for h in hits]

    @staticmethod
    def _check(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            raise OpenSearchError(f"OpenSearch {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}


def _to_filter(flt: SearchFilter) -> list[dict]:
    out: list[dict] = []
    if flt.corp_codes:
        out.append({"terms": {"corp_code": flt.corp_codes}})
    if flt.year_from is not None or flt.year_to is not None:
        rng = {}
        if flt.year_from is not None:
            rng["gte"] = flt.year_from
        if flt.year_to is not None:
            rng["lte"] = flt.year_to
        out.append({"range": {"period_year": rng}})
    if flt.report_kinds:
        out.append({"terms": {"report_kind": flt.report_kinds}})
    return out
