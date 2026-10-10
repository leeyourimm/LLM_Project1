import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 기본 수집·색인 대상: KOSPI 대형주 15개사. dartrag collect 의 기본 대상이고,
# INDEX_SCOPE=focus 일 때는 이 회사들과 운영자가 dartrag scope add 로 더한 회사만 자동 색인한다
DEFAULT_STOCKS = (
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "373220",  # LG에너지솔루션
    "207940",  # 삼성바이오로직스
    "005380",  # 현대차
    "000270",  # 기아
    "068270",  # 셀트리온
    "005490",  # POSCO홀딩스
    "035420",  # NAVER
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "105560",  # KB금융
    "055550",  # 신한지주
    "035720",  # 카카오
    "012330",  # 현대모비스
)

# 채팅 첫 화면의 예시 질문 (기본 수집 대상 15개사 안의 회사). 작업자가 답을 답변 캐시에 미리
# 넣어 두어 CPU 서버에서도 누르면 바로 답한다 (dartrag cache warm).
# .env 의 EXAMPLE_QUESTIONS 로 바꾼다
DEFAULT_EXAMPLES = (
    "삼성전자 2024년 매출액과 영업이익률은?",
    "SK하이닉스 주요 사업 부문은?",
    "현대차 사업보고서에 나온 주요 위험 요인은?",
)


def parse_examples(raw: str) -> tuple[str, ...]:
    """EXAMPLE_QUESTIONS 값 → 예시 질문들. | 로 나누거나 JSON 목록. 비어 있으면 기본 예시."""
    raw = (raw or "").strip()
    if raw.startswith("["):
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ValueError("EXAMPLE_QUESTIONS 는 문자열 목록이어야 합니다")
    else:
        items = raw.split("|")
    questions = tuple(q for q in (str(i).strip() for i in items) if q)
    return questions or DEFAULT_EXAMPLES


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
    # LLM 게이트웨이 (answer/gateway.py): 시간 제한, 일시적 오류 재시도, 대체 모델
    # 대체 모델은 LLM_FALLBACK_MODEL 을 넣을 때만 쓴다 (예: 더 작은 qwen3:1.7b, 미리 ollama pull)
    llm_fallback_model: str = ""
    llm_timeout: float = 300  # 요청 하나가 끝날 때까지 기다리는 최대 시간(초). 0 이면 제한 없음
    # 대체 모델이 있을 때만: 기본 모델이 이 시간(초) 안에 첫 글자를 못 내면 대체 모델로 넘긴다
    llm_first_token_timeout: float = 30
    llm_retries: int = 2  # 연결 실패·5xx 같은 일시적 오류에 다시 보낼 횟수
    llm_retry_backoff: float = 0.5  # 첫 재시도 전 기다리는 시간(초). 다시 할 때마다 두 배

    # 답변 캐시 (Redis). redis_url 을 비우면 캐시 없이 동작
    redis_url: str = "redis://localhost:6379/0"
    answer_cache: bool = True
    semantic_cache: bool = True
    answer_cache_ttl: int = 7 * 86400
    # 기업 대시보드 캐시 보관 시간(초). 회사 데이터가 바뀌면 이보다 먼저 새로 만든다
    dashboard_cache_ttl: int = 6 * 3600
    # 채팅 첫 화면의 예시 질문. | 로 나누거나 JSON 목록으로 넣는다. 비우면 기본 예시
    # 예: EXAMPLE_QUESTIONS=삼성전자 2024년 매출액은?|SK하이닉스 주요 사업 부문은?
    example_questions: str = ""
    # 예시 질문의 답을 답변 캐시에 미리 넣어 두는 주기(분). 데이터가 바뀐 질문만 다시 만든다.
    # 0 이면 끔
    example_warm_minutes: int = 30

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

    # 웹 푸시 (VAPID 키, dartrag push keys 로 만들어 .env 에 넣는다). 비우면 웹 푸시를 쓰지 않음
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    # 푸시 서비스가 문제 있을 때 연락할 곳 (mailto: 또는 https:). 비우면 PUBLIC_URL
    vapid_subject: str = ""

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
    # 가입 없이 체험하기 (로그인을 켰을 때만). 이메일·비밀번호 없는 체험 계정을 만들고,
    # GUEST_HOURS 가 지나면 작업자가 기록과 함께 지운다
    allow_guest: bool = False
    guest_hours: int = 24

    # 작업자(Celery). 주기는 분 단위
    feed_poll_minutes: int = 10
    ingest_minutes: int = 5
    alerts_minutes: int = 5
    # 관심 종목 회사의 대시보드를 미리 만드는 주기 (바뀐 회사만 다시 만든다)
    dashboard_warm_minutes: int = 15
    # 새 정기보고서를 자동으로 받아 원문·청크·검색 색인까지 만드는 회사 범위.
    # all: 모든 상장사. focus: 기본 15개사(DEFAULT_STOCKS)와 dartrag scope add 로 더한 회사만.
    # 공시 피드와 알림은 작은 목록이라 어느 쪽이든 모든 상장사를 받는다
    index_scope: Literal["all", "focus"] = "all"
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
    # 체험 계정: 만들기는 접속 주소별로 센다. 질문·무거운 요청은 위 한도에 더해 체험 계정이 쓰는
    # 접속 주소별로도 세서, 체험 계정을 새로 만들어도 한도가 다시 차지 않는다
    rate_guest_per_hour: int = 5
    rate_guest_ask_per_minute: int = 2
    rate_guest_ask_per_day: int = 20
    rate_guest_heavy_per_hour: int = 3

    # 운영 관측. 모두 비우면 꺼진다
    environment: str = "development"  # development / production
    metrics_token: str = ""  # 넣으면 /metrics 에 Authorization: Bearer <값> 이 필요
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    langfuse_host: str = ""  # 예: http://localhost:3001
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_sample_rate: float = 1.0
    # 탈퇴할 때 그 사용자의 Langfuse 추적 삭제를 요청 (실패해도 탈퇴는 그대로 끝남)
    langfuse_delete_on_account_delete: bool = True

    @property
    def index_focus(self) -> tuple[str, ...] | None:
        """자동 색인의 기본 대상 종목코드. None 이면 모든 상장사 (INDEX_SCOPE=all).

        focus 일 때는 여기에 더해 dartrag scope add 로 DB(index_scope)에 넣은 회사도 대상이다."""
        return None if self.index_scope == "all" else DEFAULT_STOCKS

    @property
    def examples(self) -> tuple[str, ...]:
        """채팅 첫 화면과 답변 캐시 미리 넣기가 함께 쓰는 예시 질문."""
        return parse_examples(self.example_questions)


@lru_cache
def get_settings() -> Settings:
    return Settings()
