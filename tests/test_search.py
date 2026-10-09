import json

import httpx
import pytest
import respx
from qdrant_client import QdrantClient

from dartrag.search import HybridRetriever, IndexedChunk, KeywordIndex, SearchFilter, VectorIndex
from dartrag.search.keyword import OpenSearchError
from dartrag.search.vector import point_id


def test_rrf_rewards_agreement_between_rankings():
    from dartrag.search import rrf

    fused = rrf([["a", "b"], ["c", "b"]], k=60)
    # 두 검색 모두 2위인 b 가, 한쪽에서만 1위인 a·c 보다 앞선다
    assert [cid for cid, _ in fused] == ["b", "a", "c"]
    assert fused[0][1] == pytest.approx(2 / 62)


class FakeEmbedder:
    """단어 포함 여부로 만드는 결정적 벡터 (모델 다운로드 없이 테스트)."""

    name = "fake"
    vocab = ["매출", "반도체", "배당", "소송", "hbm", "자동차"]
    dim = len(vocab) + 1

    def _vec(self, text: str) -> list[float]:
        v = [1.0 if w in text.lower() else 0.0 for w in self.vocab] + [0.1]
        norm = sum(x * x for x in v) ** 0.5
        return [x / norm for x in v]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def chunk(cid, text, corp="00126380", year="2024.12", kind="사업보고서"):
    return IndexedChunk(
        chunk_id=cid,
        rcept_no=f"2025031100000{cid[-1]}",
        corp_code=corp,
        corp_name="삼성전자" if corp == "00126380" else "현대차",
        report_kind=kind,
        period_key=year,
        kind="text",
        section_path=["II. 사업의 내용"],
        text=text,
    )


CHUNKS = [
    chunk("c1", "반도체 매출이 HBM 판매 증가로 늘었다"),
    chunk("c2", "배당 정책: 연간 배당을 유지한다"),
    chunk("c3", "반도체 매출 감소", year="2023.12"),
    chunk("c4", "자동차 매출이 증가했다", corp="00164742"),
]


@pytest.fixture
def vector():
    emb = FakeEmbedder()
    idx = VectorIndex(QdrantClient(":memory:"))
    idx.ensure(emb.dim)
    idx.upsert(CHUNKS, emb.embed_documents([c.text for c in CHUNKS]))
    return idx


def test_vector_search_with_filters(vector):
    q = FakeEmbedder().embed_query("반도체 매출")
    assert vector.search(q, SearchFilter(), 2) == ["c3", "c1"]
    assert vector.search(q, SearchFilter(year_from=2024), 4) == ["c1", "c4", "c2"]
    assert vector.search(q, SearchFilter(year_to=2023), 4) == ["c3"]
    assert "c4" not in vector.search(q, SearchFilter(corp_codes=["00126380"]), 4)
    assert vector.search(q, SearchFilter(corp_codes=["00164742"]), 4) == ["c4"]


def test_vector_delete_filing_and_dim_check(vector):
    vector.delete_filing("20250311000001")
    q = FakeEmbedder().embed_query("반도체 매출")
    assert "c1" not in vector.search(q, SearchFilter(), 10)
    with pytest.raises(ValueError, match="차원"):
        vector.ensure(1024)
    assert point_id("c1") == point_id("c1") != point_id("c2")


class FakeKeyword:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def search(self, query, flt, limit):
        self.calls.append((query, flt, limit))
        return self.result


def test_hybrid_fuses_and_loads_chunks(vector):
    keyword = FakeKeyword(["c3", "c9"])  # c9: 인덱스에만 남은 지워진 청크
    loaded = {cid: {"body": cid} for cid in ("c1", "c2", "c3", "c4")}
    retriever = HybridRetriever(
        FakeEmbedder(),
        vector,
        keyword,
        lambda ids: {i: loaded[i] for i in ids if i in loaded},
        candidates=10,
    )
    hits = retriever.search("반도체 매출", SearchFilter(corp_codes=["00126380"]), limit=3)
    ids = [h.chunk_id for h in hits]
    assert ids[0] == "c3"  # 벡터·키워드 양쪽에서 상위
    assert "c9" not in ids
    top = hits[0]
    assert top.dense_rank is not None and top.keyword_rank == 1
    assert top.chunk == {"body": "c3"}
    assert keyword.calls[0][1].corp_codes == ["00126380"]


BASE = "http://os:9200"


@respx.mock
def test_keyword_index_requests():
    head = respx.head(f"{BASE}/dart_chunks").mock(return_value=httpx.Response(404))
    put = respx.put(f"{BASE}/dart_chunks").mock(
        return_value=httpx.Response(200, json={"acknowledged": True})
    )
    bulk = respx.post(f"{BASE}/_bulk").mock(
        return_value=httpx.Response(200, json={"errors": False, "items": []})
    )
    search = respx.post(f"{BASE}/dart_chunks/_search").mock(
        return_value=httpx.Response(200, json={"hits": {"hits": [{"_source": {"chunk_id": "c1"}}]}})
    )
    idx = KeywordIndex.connect(BASE)
    idx.ensure()
    assert head.called
    settings = json.loads(put.calls[0].request.content)["settings"]
    assert settings["analysis"]["tokenizer"]["korean"]["type"] == "nori_tokenizer"

    idx.upsert(CHUNKS[:2])
    lines = bulk.calls[0].request.content.decode().strip().split("\n")
    assert len(lines) == 4
    assert json.loads(lines[0]) == {"index": {"_index": "dart_chunks", "_id": "c1"}}
    doc = json.loads(lines[1])
    assert doc["text"].startswith("반도체") and doc["period_year"] == 2024

    flt = SearchFilter(corp_codes=["00126380"], year_from=2023, report_kinds=["사업보고서"])
    assert idx.search("HBM 매출", flt, 5) == ["c1"]
    body = json.loads(search.calls[0].request.content)
    assert body["size"] == 5
    assert body["query"]["bool"]["filter"] == [
        {"terms": {"corp_code": ["00126380"]}},
        {"range": {"period_year": {"gte": 2023}}},
        {"terms": {"report_kind": ["사업보고서"]}},
    ]


@respx.mock
def test_keyword_index_reports_errors():
    respx.post(f"{BASE}/_bulk").mock(
        return_value=httpx.Response(
            200,
            json={"errors": True, "items": [{"index": {"error": {"type": "mapper_parsing"}}}]},
        )
    )
    respx.post(f"{BASE}/dart_chunks/_search").mock(return_value=httpx.Response(500, text="boom"))
    idx = KeywordIndex.connect(BASE)
    with pytest.raises(OpenSearchError, match="색인 실패 1건"):
        idx.upsert(CHUNKS[:1])
    with pytest.raises(OpenSearchError, match="500"):
        idx.search("x", SearchFilter(), 5)
