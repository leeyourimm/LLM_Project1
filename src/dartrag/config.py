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
    # 운영자 본인에게 보내는 텔레그램·이메일 알림 (비우면 쓰지 않음)
    alert_telegram_chat_id: str = ""
    alert_email_to: str = ""

    # 텔레그램 봇 (BotFather 에서 받은 토큰). 사용자별 알림에도 쓴다
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""  # 공개 서버에서 웹훅을 쓸 때만

    # 메일 발송 (SMTP). 사용자별 이메일 알림과 인증 메일에 쓴다
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True

    # 메일 속 링크(인증, 구독 취소)에 쓰는 서비스 주소와 서명용 비밀값
    public_url: str = "http://127.0.0.1:8000"
    allowed_origins: str = ""  # 쉼표로 구분, 예: http://localhost:3000
    secret_key: str = ""

    # 로그인. 인터넷에 공개할 때만 AUTH_REQUIRED=true 로 켠다
    auth_required: bool = False
    allow_signup: bool = True
    cookie_secure: bool = False  # https 로 서비스할 때 true
    session_days: int = 30
    # 가입한 이메일을 인증해야 이메일 알림을 켤 수 있게 할지. 메일 발송(SMTP)이 있어야 적용된다
    email_verification_required: bool = False

    # 작업자(Celery). 주기는 분 단위
    feed_poll_minutes: int = 10
    ingest_minutes: int = 5
    alerts_minutes: int = 5
    backfill_enabled: bool = False  # 전체 상장사 과거 데이터 채우기 (며칠 걸림)
    backfill_start_year: int = 2015
    backfill_batch: int = 20  # 한 번에 처리할 회사 수
    dart_daily_limit: int = 20_000  # OpenDART 키당 하루 호출 한도
    dart_reserve: int = 3_000  # 과거 데이터 채우기가 남겨 둘 호출 수 (새 공시 처리용)
    conversation_retention_days: int = 180

    # PDF 리포트 한글 글꼴(TTF). 비우면 나눔고딕·애플고딕·맑은 고딕을 차례로 찾는다
    report_font: str | None = None

    # 정기 평가: 일요일 새벽에 평가 문항 일부로 답변 품질을 재고 기록한다 (LLM 시간이 듦)
    eval_schedule_enabled: bool = False
    eval_schedule_cases: int = 60

    # 요청 한도 (사용자별, 로그인하지 않았으면 접속 주소별). 0 이면 끔
    rate_ask_per_minute: int = 6
    rate_ask_per_day: int = 200
    rate_heavy_per_hour: int = 10  # 비교 설명, 변경점 요약 새로 만들기, 요약이 든 PDF
    # 가입·비밀번호 변경·재설정 메일·인증 메일 다시 받기 (로그인 시도는 따로 제한)
    rate_auth_per_hour: int = 20

    # 운영 관측. 모두 비우면 꺼진다
    environment: str = "development"  # development / production
    metrics_token: str = ""  # 넣으면 /metrics 에 Authorization: Bearer <값> 이 필요
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    langfuse_host: str = ""  # 예: http://localhost:3001
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_sample_rate: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
