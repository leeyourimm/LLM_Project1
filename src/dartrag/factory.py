"""검색기·답변기 조립. 웹 서버, CLI, 평가, 워커가 같은 설정으로 만든다."""

import logging
import threading

from dartrag.config import Settings
from dartrag.db import Repository

log = logging.getLogger(__name__)


class Backends:
    """무거운 모델·클라이언트는 처음 쓸 때 한 번만 만든다 (스레드 안전)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._cache: dict = {}

    def _get(self, name: str, make):
        with self._lock:
            if name not in self._cache:
                self._cache[name] = make()
            return self._cache[name]

    @property
    def embedder(self):
        from dartrag.search import SentenceTransformerEmbedder

        return self._get("embedder", lambda: SentenceTransformerEmbedder(self.settings.embed_model))

    @property
    def qdrant(self):
        from qdrant_client import QdrantClient

        return self._get("qdrant", lambda: QdrantClient(url=self.settings.qdrant_url))

    @property
    def vector(self):
        from dartrag.search import VectorIndex

        return self._get("vector", lambda: VectorIndex(self.qdrant))

    @property
    def keyword(self):
        from dartrag.search import KeywordIndex

        return self._get("keyword", lambda: KeywordIndex.connect(self.settings.opensearch_url))

    @property
    def reranker(self):
        if not self.settings.rerank_model:
            return None
        from dartrag.search.rerank import CrossEncoderReranker

        return self._get("reranker", lambda: CrossEncoderReranker(self.settings.rerank_model))

    @property
    def redis(self):
        if not self.settings.redis_url:
            return None
        import redis

        return self._get("redis", lambda: redis.Redis.from_url(self.settings.redis_url))


def build_retriever(backends: Backends, repo: Repository):
    from dartrag.search import HybridRetriever

    s = backends.settings
    return HybridRetriever(
        backends.embedder,
        backends.vector,
        backends.keyword,
        repo.get_chunks,
        reranker=backends.reranker,
        recency_weight=s.recency_weight,
        expand=repo.expand_chunks if s.expand_context else None,
    )


def build_answerer(
    backends: Backends, repo: Repository, *, use_cache: bool = True, use_fallback: bool = True
):
    """use_fallback=False: 대체 모델을 쓰지 않는다 (평가는 기본 모델의 답만 재야 한다)."""
    from dartrag.answer import Answerer
    from dartrag.finance import FinanceTool

    s = backends.settings
    llm = build_llm(s, fallback=use_fallback)
    cache = None
    if use_cache and s.answer_cache and backends.redis is not None:
        from dartrag.answer.cache import AnswerCache

        cache = AnswerCache(
            backends.redis,
            llm.name,
            repo.data_version,
            ttl=s.answer_cache_ttl,
            qdrant=backends.qdrant if s.semantic_cache else None,
            embed=backends.embedder.embed_query if s.semantic_cache else None,
        )
    from dartrag.obs.tracing import get_tracer

    return Answerer(
        build_retriever(backends, repo),
        llm,
        finance=FinanceTool(repo),
        cache=cache,
        tracer=get_tracer(s),
    )


def build_llm(settings: Settings, *, fallback: bool = True):
    """답변 모델(Ollama)을 게이트웨이로 감싼다: 시간 제한, 일시적 오류 재시도, 대체 모델."""
    import httpx

    from dartrag.answer import OllamaLLM
    from dartrag.answer.gateway import LLMGateway

    s = settings
    # 시간 제한은 게이트웨이가 잰다. httpx 의 읽기 제한은 그보다 조금 길게 두어
    # 게이트웨이가 포기한 요청의 연결을 정리하는 데만 쓴다
    read = s.llm_timeout + 30 if s.llm_timeout else None
    http_timeout = httpx.Timeout(read, connect=10)
    primary = OllamaLLM(s.llm_model, s.ollama_url, timeout=http_timeout)
    backup = s.llm_fallback_model.strip()
    use_backup = fallback and backup and backup != s.llm_model
    return LLMGateway(
        primary,
        OllamaLLM(backup, s.ollama_url, timeout=http_timeout) if use_backup else None,
        timeout=s.llm_timeout,
        first_token_timeout=s.llm_first_token_timeout,
        retries=s.llm_retries,
        backoff=s.llm_retry_backoff,
    )


def build_senders(settings: Settings) -> dict:
    """설정된 발송 수단만: {"email": EmailSender, "telegram": TelegramSender,
    "push": WebPushSender}."""
    from dartrag.feed.channels import EmailSender, TelegramSender, WebPushSender

    senders: dict = {}
    if settings.smtp_host and settings.smtp_from:
        senders["email"] = EmailSender(
            settings.smtp_host,
            settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.smtp_from,
            starttls=settings.smtp_starttls,
        )
    if settings.telegram_bot_token:
        senders["telegram"] = TelegramSender(settings.telegram_bot_token)
    if settings.vapid_private_key and settings.vapid_public_key:
        try:
            senders["push"] = WebPushSender(
                settings.vapid_private_key,
                settings.vapid_public_key,
                settings.vapid_subject or settings.public_url,
            )
        except ValueError as e:  # 오류 문구에는 키 값을 넣지 않는다
            log.warning("웹 푸시를 켜지 못했습니다: %s", e)
    return senders


def build_notifiers(settings: Settings, echo=print) -> list:
    """운영자 본인용 알림 채널. 아무것도 설정하지 않으면 터미널 출력."""
    from dartrag.feed.notify import (
        ConsoleNotifier,
        EmailNotifier,
        TelegramNotifier,
        WebhookNotifier,
    )

    senders = build_senders(settings)
    out: list = []
    if settings.alert_webhook_url:
        out.append(WebhookNotifier(settings.alert_webhook_url))
    if settings.alert_telegram_chat_id and "telegram" in senders:
        out.append(TelegramNotifier(senders["telegram"], settings.alert_telegram_chat_id))
    if settings.alert_email_to and "email" in senders:
        out.append(EmailNotifier(senders["email"], settings.alert_email_to))
    return out or [ConsoleNotifier(echo)]
