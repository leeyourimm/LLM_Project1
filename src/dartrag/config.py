from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dart_api_key: str = ""
    dart_min_interval: float = 0.2
    database_url: str = "postgresql://dartrag:dartrag@localhost:5432/dartrag"

    raw_store: Literal["local", "s3"] = "local"
    raw_store_dir: Path = Path("./data/raw")
    s3_endpoint_url: str | None = None
    s3_bucket: str = "dart-raw"

    qdrant_url: str = "http://localhost:6333"
    opensearch_url: str = "http://localhost:9200"
    embed_model: str = "BAAI/bge-m3"
    # 리랭커. 비우면 끔 (메모리가 부족할 때)
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    recency_weight: float = 0.1
    expand_context: bool = True

    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:8b"

    # 답변 캐시 (Redis). redis_url 을 비우면 캐시 없이 동작
    redis_url: str = "redis://localhost:6379/0"
    answer_cache: bool = True
    semantic_cache: bool = True
    answer_cache_ttl: int = 7 * 86400

    # 공시 알림 웹훅 (Slack·Discord). 비워 두면 터미널에만 출력
    alert_webhook_url: str = ""

    # 로그인. 인터넷에 공개할 때만 AUTH_REQUIRED=true 로 켠다
    auth_required: bool = False
    allow_signup: bool = True
    cookie_secure: bool = False  # https 로 서비스할 때 true
    session_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
