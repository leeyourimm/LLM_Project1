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


class FixedDense:
    def __init__(self, by_corp):
        self.by_corp = by_corp
        self.calls = []

    def search(self, vector, flt, limit):
        self.calls.append(flt.corp_codes)
        if len(flt.corp_codes) == 1:
            return self.by_corp[flt.corp_codes[0]]
        return [c for ids in self.by_corp.values() for c in ids]


def loader(chunks):
    return lambda ids: {i: dict(chunks[i]) for i in ids if i in chunks}


def test_hybrid_splits_multi_company_questions():
    dense = FixedDense({"A": ["a1", "a2", "a3", "a4"], "B": ["b1", "b2"]})
    chunks = {c: {"body": c, "period_key": "2024.12"} for c in ["a1", "a2", "a3", "a4", "b1", "b2"]}
    retriever = HybridRetriever(FakeEmbedder(), dense, FakeKeyword([]), loader(chunks))
    hits = retriever.search("비교", SearchFilter(corp_codes=["A", "B"]), limit=4)
    assert [h.chunk_id for h in hits] == ["a1", "b1", "a2", "b2"]  # 회사별로 번갈아
    assert dense.calls == [["A"], ["B"]]


def test_hybrid_dedupes_repeated_paragraphs_and_prefers_recent():
    chunks = {
        "old": {"body": "회사는  반도체 사업을 한다", "period_key": "2022.12"},
        "new": {"body": "회사는 반도체 사업을 한다", "period_key": "2024.12"},
        "mid": {"body": "다른 문단", "period_key": "2023.12"},
    }
    dense = FixedDense({"A": ["old", "mid", "new"]})
    retriever = HybridRetriever(FakeEmbedder(), dense, FakeKeyword([]), loader(chunks))
    hits = retriever.search("반도체", SearchFilter(corp_codes=["A"]), limit=5)
    assert [h.chunk_id for h in hits] == ["new", "mid"]
    assert hits[0].duplicates == 1


class FakeReranker:
    name = "fake"

    def __init__(self):
        self.texts = []

    def score(self, query, texts):
        self.texts = texts
        return [0.9 if "정답" in t else 0.1 for t in texts]


def test_hybrid_reranks_and_expands_context():
    chunks = {
        "x": {"body": "관계없는 글", "period_key": "2024.12", "corp_name": "삼성전자"},
        "y": {"body": "정답이 있는 글", "period_key": "2024.12", "corp_name": "삼성전자"},
    }
    rr = FakeReranker()
    retriever = HybridRetriever(
        FakeEmbedder(),
        FixedDense({"A": ["x", "y"]}),
        FakeKeyword([]),
        loader(chunks),
        reranker=rr,
        expand=lambda ids: {"y": "앞 문단\n정답이 있는 글\n뒤 문단"},
    )
    hits = retriever.search("질문", SearchFilter(corp_codes=["A"]), limit=2)
    assert [h.chunk_id for h in hits] == ["y", "x"] and hits[0].rerank_score == 0.9
    assert rr.texts[0].startswith("삼성전자")
    assert (
        hits[0].chunk["context_body"].startswith("앞 문단") and "context_body" not in hits[1].chunk
    )


def test_recency_breaks_ties():
    chunks = {
        "old": {"body": "a", "period_key": "2020.12"},
        "new": {"body": "b", "period_key": "2024.12"},
    }
    retriever = HybridRetriever(
        FakeEmbedder(), FixedDense({"A": ["old"]}), FakeKeyword(["new"]), loader(chunks)
    )
    # 두 청크 모두 한쪽 검색에서만 1위라 RRF 점수가 같다 → 최신이 앞
    assert [h.chunk_id for h in retriever.search("q", SearchFilter(corp_codes=["A"]))] == [
        "new",
        "old",
    ]


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


def test_models_load_on_first_use(monkeypatch):
    """검색기를 만들기만 해서는 모델(각 약 2GB)을 불러오지 않는다."""
    import sys
    import types

    from dartrag.search import SentenceTransformerEmbedder
    from dartrag.search.rerank import CrossEncoderReranker

    loaded = []

    class Model:
        def __init__(self, name, **kw):
            loaded.append(name)

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kw):
            return [types.SimpleNamespace(tolist=lambda: [1.0, 0.0, 0.0]) for _ in texts]

        def predict(self, pairs):
            return [0.5 for _ in pairs]

    fake = types.SimpleNamespace(SentenceTransformer=Model, CrossEncoder=Model)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    embedder, reranker = SentenceTransformerEmbedder("emb"), CrossEncoderReranker("rr")
    assert loaded == [] and embedder.name == "emb" and reranker.name == "rr"
    assert embedder.embed_query("매출") == [1.0, 0.0, 0.0] and embedder.dim == 3
    assert reranker.score("매출", ["a", "b"]) == [0.5, 0.5]
    assert loaded == ["emb", "rr"]  # 한 번만 불러온다
