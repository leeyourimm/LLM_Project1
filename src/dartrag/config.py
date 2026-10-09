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

    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:8b"

    # 공시 알림 웹훅 (Slack·Discord). 비워 두면 터미널에만 출력
    alert_webhook_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
