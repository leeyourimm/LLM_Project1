"""답변 캐시.

1단계: 같은 질문(공백·대소문자만 다른 경우 포함)은 Redis 에서 바로 꺼낸다.
2단계(시맨틱): 뜻이 거의 같은 질문("삼성전자 2024 매출액은?" / "2024년 삼성전자 매출 얼마야")은
         Qdrant 에 저장한 질문 벡터로 찾는다. 숫자(연도 등)가 하나라도 다르면 쓰지 않는다.

키에 모델, 프롬프트 버전, 데이터 버전이 들어가므로 새 공시를 색인하거나
프롬프트를 바꾸면 예전 답은 자동으로 쓰이지 않는다.
"""

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict

from dartrag.answer.prompt import PROMPT_VERSION
from dartrag.answer.service import Answer, Citation
from dartrag.search import SearchFilter, SearchHit

log = logging.getLogger(__name__)
SEMANTIC_COLLECTION = "answer_cache"


def normalize_question(q: str) -> str:
    q = re.sub(r"\s+", " ", q.strip().lower())
    return re.sub(r"[?？!.\s]+$", "", q)


def _numbers(q: str) -> list[str]:
    return sorted(re.findall(r"\d+", q))


def answer_to_dict(a: Answer) -> dict:
    return {
        "question": a.question,
        "text": a.text,
        "found": a.found,
        "unverified": a.unverified,
        "warnings": a.warnings,
        "refused": a.refused,
        "hits": [asdict(h) for h in a.hits],
        "citations": [c.number for c in a.citations],
        "invalid_citations": a.invalid_citations,
        "numbers_checked": a.numbers_checked,
    }


def answer_from_dict(d: dict) -> Answer:
    hits = [SearchHit(**h) for h in d["hits"]]
    return Answer(
        question=d["question"],
        text=d["text"],
        citations=[Citation(n, hits[n - 1]) for n in d["citations"] if 1 <= n <= len(hits)],
        hits=hits,
        found=d["found"],
        unverified=d["unverified"],
        warnings=d["warnings"],
        refused=d.get("refused"),
        # 이 항목이 생기기 전에 저장한 답에는 없다
        invalid_citations=d.get("invalid_citations", []),
        numbers_checked=d.get("numbers_checked", 0),
        cached=True,
    )


def prune_semantic(qdrant, ttl: int, now: float | None = None) -> int:
    """보관 기간(ttl)이 지난 질문 벡터를 지우고 지운 개수를 돌려준다.

    답 본문은 Redis 에서 ttl 뒤 저절로 사라지고 조회도 기간 안의 것만 보지만, Qdrant 의 질문
    벡터는 지우지 않으면 질문 수만큼 계속 쌓여 메모리를 쓴다. 작업자가 매일 새벽 정리한다."""
    from qdrant_client import models

    if not qdrant.collection_exists(SEMANTIC_COLLECTION):
        return 0
    cutoff = (time.time() if now is None else now) - ttl
    expired = models.Filter(
        must=[models.FieldCondition(key="created", range=models.Range(lt=cutoff))]
    )
    n = qdrant.count(SEMANTIC_COLLECTION, count_filter=expired, exact=True).count
    if n:
        qdrant.delete(SEMANTIC_COLLECTION, points_selector=models.FilterSelector(filter=expired))
    return n


class AnswerCache:
    def __init__(
        self,
        redis,
        model: str,
        data_version: Callable[[], int],
        *,
        ttl: int = 7 * 86400,
        qdrant=None,
        embed: Callable[[str], list[float]] | None = None,
        threshold: float = 0.95,
    ):
        self.redis = redis
        self.model = model
        self.data_version = data_version
        self.ttl = ttl
        self.qdrant = qdrant
        self.embed = embed
        self.threshold = threshold
        self._collection_ready = False

    def _scope(self, flt: SearchFilter) -> str:
        parts = {
            "corp": sorted(flt.corp_codes),
            "from": flt.year_from,
            "to": flt.year_to,
            "kinds": sorted(flt.report_kinds),
            "model": self.model,
            "prompt": PROMPT_VERSION,
            "data": self.data_version(),
        }
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]

    def _key(self, scope: str, question: str) -> str:
        q = hashlib.sha256(normalize_question(question).encode()).hexdigest()[:32]
        return f"dartrag:answer:{scope}:{q}"

    def get(self, question: str, flt: SearchFilter) -> Answer | None:
        try:
            scope = self._scope(flt)
            raw = self.redis.get(self._key(scope, question))
            if raw:
                return answer_from_dict(json.loads(raw))
            return self._semantic_get(scope, question, flt)
        except Exception as e:  # noqa: BLE001 - 캐시가 고장 나도 답변은 계속 나가야 한다
            log.warning("답변 캐시 조회 실패: %s", type(e).__name__)
            return None

    def has(self, question: str, flt: SearchFilter) -> bool:
        """지금 데이터 버전으로 이 질문(공백·대소문자만 다른 것 포함)의 답이 저장돼 있는지.

        뜻이 비슷한 질문은 찾지 않는다 (예시 질문 미리 채우기용, 임베딩 모델을 부르지 않음)."""
        try:
            return bool(self.redis.exists(self._key(self._scope(flt), question)))
        except Exception as e:  # noqa: BLE001
            log.warning("답변 캐시 조회 실패: %s", type(e).__name__)
            return False

    def put(self, question: str, flt: SearchFilter, answer: Answer) -> None:
        if answer.refused:
            return
        try:
            scope = self._scope(flt)
            payload = json.dumps(answer_to_dict(answer), ensure_ascii=False, default=str)
            self.redis.set(self._key(scope, question), payload, ex=self.ttl)
            self._semantic_put(scope, question, flt)
        except Exception as e:  # noqa: BLE001
            log.warning("답변 캐시 저장 실패: %s", type(e).__name__)

    # --- 시맨틱 --------------------------------------------------------------

    def _semantic_enabled(self, flt: SearchFilter) -> bool:
        # 회사가 정해지지 않은 질문은 회사만 다른 비슷한 질문과 섞일 수 있어 쓰지 않는다
        return bool(self.qdrant and self.embed and flt.corp_codes)

    def _ensure(self, dim: int) -> None:
        if self._collection_ready:
            return
        from qdrant_client import models

        if not self.qdrant.collection_exists(SEMANTIC_COLLECTION):
            self.qdrant.create_collection(
                SEMANTIC_COLLECTION,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
        self._collection_ready = True

    def _semantic_get(self, scope: str, question: str, flt: SearchFilter) -> Answer | None:
        if not self._semantic_enabled(flt):
            return None
        from qdrant_client import models

        vector = self.embed(question)
        self._ensure(len(vector))
        result = self.qdrant.query_points(
            SEMANTIC_COLLECTION,
            query=vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(key="scope", match=models.MatchValue(value=scope)),
                    models.FieldCondition(
                        key="created", range=models.Range(gte=time.time() - self.ttl)
                    ),
                ]
            ),
            limit=3,
            with_payload=True,
        ).points
        for p in result:
            if p.score < self.threshold or p.payload.get("numbers") != _numbers(question):
                continue
            raw = self.redis.get(p.payload["key"])
            if raw:
                return answer_from_dict(json.loads(raw))
        return None

    def _semantic_put(self, scope: str, question: str, flt: SearchFilter) -> None:
        if not self._semantic_enabled(flt):
            return
        from qdrant_client import models

        vector = self.embed(question)
        self._ensure(len(vector))
        self.qdrant.upsert(
            SEMANTIC_COLLECTION,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, self._key(scope, question))),
                    vector=vector,
                    payload={
                        "scope": scope,
                        "key": self._key(scope, question),
                        "numbers": _numbers(question),
                        "created": time.time(),
                    },
                )
            ],
        )
