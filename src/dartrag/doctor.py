"""실행 전 준비 상태 점검 (dartrag doctor).

빠진 것이 있으면 무엇을 하면 되는지 한 줄 명령으로 알려준다.
인증키 같은 비밀 값은 출력하지 않는다.
"""

import importlib.util
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from dartrag.config import Settings

DART_KEY_ERRORS = {
    "010": "등록되지 않은 인증키입니다",
    "011": "사용할 수 없는 인증키입니다",
    "012": "접근할 수 없는 IP입니다",
    "020": "오늘 요청 한도를 넘었습니다. 내일 다시 하세요",
    "800": "OpenDART 시스템 점검 중입니다. 점검이 끝난 뒤 다시 하세요",
}
MIN_FREE_GB = 15
DOCKER_HINT = ". Docker Desktop 이 켜져 있어야 합니다"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""  # 터미널에 그대로 붙여 넣을 수 있는 명령 또는 짧은 안내
    required: bool = True


def check_env_file(settings: Settings, env_path: Path = Path(".env")) -> Check:
    if not env_path.exists():
        return Check(".env 파일", False, "없습니다", "cp .env.example .env")
    if not settings.dart_api_key:
        return Check(
            ".env 파일",
            False,
            "DART_API_KEY 가 비어 있습니다",
            ".env 파일을 열어 DART_API_KEY= 뒤에 인증키를 넣으세요",
        )
    return Check(".env 파일", True, "인증키 있음")


def check_opendart(settings: Settings, http: httpx.Client) -> Check:
    name = "OpenDART 연결"
    if not settings.dart_api_key:
        return Check(name, False, "인증키가 없어 건너뜀", "위의 .env 파일부터 고치세요")
    end = date.today()
    params = {
        "crtfc_key": settings.dart_api_key,
        "corp_code": "00126380",
        "bgn_de": (end - timedelta(days=7)).strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_count": 1,
    }
    try:
        resp = http.get("https://opendart.fss.or.kr/api/list.json", params=params, timeout=15)
        status = resp.json().get("status")
    except (httpx.HTTPError, ValueError) as e:
        return Check(name, False, f"연결 실패 ({type(e).__name__})", "인터넷 연결을 확인하세요")
    if status in ("000", "013"):
        return Check(name, True, "인증키 정상")
    message = DART_KEY_ERRORS.get(status, f"오류 코드 {status}")
    return Check(name, False, message, "https://opendart.fss.or.kr 에서 인증키 상태를 확인하세요")


def check_postgres(settings: Settings, connect: Callable | None = None) -> Check:
    from dartrag.db import Repository

    connect = connect or Repository.connect
    try:
        repo = connect(settings.database_url)
    except Exception as e:  # noqa: BLE001 - 어떤 이유든 연결 실패로 보여준다
        return Check("Postgres", False, f"연결 실패 ({type(e).__name__}){DOCKER_HINT}", "make up")
    try:
        repo.migrate()
    finally:
        repo.conn.close()
    return Check("Postgres", True, "연결, 스키마 정상")


def check_qdrant(settings: Settings, http: httpx.Client) -> Check:
    try:
        http.get(f"{settings.qdrant_url}/collections", timeout=5).raise_for_status()
    except httpx.HTTPError as e:
        return Check(
            "Qdrant (벡터 검색)",
            False,
            f"연결 실패 ({type(e).__name__}){DOCKER_HINT}",
            "make up-search",
        )
    return Check("Qdrant (벡터 검색)", True, "연결 정상")


def check_opensearch(settings: Settings, http: httpx.Client) -> Check:
    name = "OpenSearch (키워드 검색)"
    try:
        resp = http.get(f"{settings.opensearch_url}/_cat/plugins", timeout=5)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return Check(name, False, f"연결 실패 ({type(e).__name__}){DOCKER_HINT}", "make up-search")
    if "analysis-nori" not in resp.text:
        return Check(
            name,
            False,
            "한국어 분석기(nori)가 없습니다",
            "docker compose -f infra/docker-compose.yml --profile search up -d --build",
        )
    return Check(name, True, "연결, 한국어 분석기 정상")


def check_redis(settings: Settings, connect: Callable | None = None) -> Check:
    name = "Redis (답변 캐시·작업 큐)"
    if not settings.redis_url:
        return Check(name, True, "사용 안 함 (REDIS_URL 비어 있음)", required=False)
    try:
        if connect is None:
            import redis

            connect = redis.Redis.from_url
        connect(settings.redis_url, socket_connect_timeout=3).ping()
    except Exception as e:  # noqa: BLE001
        return Check(
            name, False, f"연결 실패 ({type(e).__name__}){DOCKER_HINT}", "make up", required=False
        )
    return Check(name, True, "연결 정상", required=False)


def check_embedding_package(find_spec: Callable = importlib.util.find_spec) -> Check:
    if find_spec("sentence_transformers") is None:
        return Check("임베딩 라이브러리", False, "설치되지 않았습니다", 'pip install -e ".[embed]"')
    return Check("임베딩 라이브러리", True, "설치됨")


def check_ollama(settings: Settings, http: httpx.Client) -> Check:
    name = "Ollama (답변 모델)"
    try:
        resp = http.get(f"{settings.ollama_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = {m.get("name", "") for m in resp.json().get("models", [])}
    except (httpx.HTTPError, ValueError) as e:
        return Check(name, False, f"연결 실패 ({type(e).__name__})", "Ollama 앱을 실행하세요")
    wanted = settings.llm_model
    if wanted not in models and f"{wanted}:latest" not in models:
        return Check(name, False, f"{wanted} 모델이 없습니다", f"ollama pull {wanted}")
    return Check(name, True, f"{wanted} 준비됨")


def check_disk(path: Path = Path("."), min_free_gb: int = MIN_FREE_GB, usage=shutil.disk_usage):
    free_gb = usage(path).free / 1024**3
    ok = free_gb >= min_free_gb
    return Check(
        "남은 저장 공간",
        ok,
        f"{free_gb:.0f}GB 남음 (권장 {min_free_gb}GB 이상)",
        "" if ok else "필요 없는 파일을 지워 공간을 확보하세요",
        required=False,
    )


def check_alerts(settings: Settings) -> Check:
    """알림 설정 점검. 설정하지 않아도 되지만, 하다 만 설정은 알려 준다."""
    problems = []
    if settings.smtp_host and not settings.smtp_from:
        problems.append("SMTP_FROM 이 비어 있습니다")
    if settings.alert_telegram_chat_id and not settings.telegram_bot_token:
        problems.append("ALERT_TELEGRAM_CHAT_ID 를 쓰려면 TELEGRAM_BOT_TOKEN 이 필요합니다")
    if settings.alert_email_to and not settings.smtp_host:
        problems.append("ALERT_EMAIL_TO 를 쓰려면 SMTP_HOST 가 필요합니다")
    if settings.auth_required and (settings.smtp_host or settings.telegram_bot_token):
        if len(settings.secret_key) < 32:
            problems.append("사용자 알림 링크 서명에 SECRET_KEY(32자 이상)가 필요합니다")
        if settings.telegram_bot_token and not settings.telegram_bot_username:
            problems.append("텔레그램 연결 링크에 TELEGRAM_BOT_USERNAME 이 필요합니다")
    channels = [
        name
        for name, on in [
            ("웹훅", settings.alert_webhook_url),
            ("텔레그램", settings.telegram_bot_token),
            ("이메일", settings.smtp_host),
        ]
        if on
    ]
    detail = ", ".join(channels) if channels else "터미널 출력만 (설정된 채널 없음)"
    return Check(
        "알림 채널",
        not problems,
        detail if not problems else "; ".join(problems),
        "" if not problems else ".env 의 알림 설정을 확인하세요",
        required=False,
    )


def run_checks(settings: Settings, http: httpx.Client | None = None, *, online: bool = True):
    own = http is None
    http = http or httpx.Client()
    try:
        checks = [check_env_file(settings)]
        if online:
            checks.append(check_opendart(settings, http))
        checks += [
            check_postgres(settings),
            check_qdrant(settings, http),
            check_opensearch(settings, http),
            check_redis(settings),
            check_embedding_package(),
            check_ollama(settings, http),
            check_alerts(settings),
            check_disk(),
        ]
    finally:
        if own:
            http.close()
    return checks


def all_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks if c.required)


def format_checks(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "✅" if c.ok else ("❌" if c.required else "⚠️")
        lines.append(f"{mark} {c.name}: {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"   → {c.fix}")
    return "\n".join(lines)
