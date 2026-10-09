"""오류 수집 (Sentry). SENTRY_DSN 을 넣을 때만 켜진다.

질문 내용, 이메일, 쿠키, 토큰이 오류 보고에 실려 나가지 않게 보내기 전에 지운다.
"""

import logging
import re

log = logging.getLogger(__name__)

# 요청 본문(질문), 쿠키, 인증 헤더, 사용자 정보는 보내지 않는다
_DROP_REQUEST_KEYS = ("data", "cookies", "query_string", "env")
_SECRET_HEADER = re.compile(r"cookie|authorization|token|secret|api-key", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BOT_TOKEN = re.compile(r"bot\d+:[\w-]{20,}")
# 주소의 쿼리 문자열에 든 비밀값 (OpenDART crtfc_key, 인증 링크 token, 구독 취소 t 등)
_QUERY_SECRET = re.compile(r"([?&](?:crtfc_key|token|t|key|secret|api_key)=)[^&\s'\"]+", re.I)
# 웹 푸시 구독 주소는 그 브라우저를 가리키는 개인 식별자라 호스트만 남긴다
_PUSH_ENDPOINT = re.compile(
    r"(https://(?:(?:fcm|android)\.googleapis\.com|[\w.-]*\.?push\.services\.mozilla\.com"
    r"|[\w.-]*\.?push\.apple\.com|[\w.-]*\.notify\.windows\.com)/)[^\s'\"]+",
    re.I,
)


def _scrub_text(text: str) -> str:
    text = _QUERY_SECRET.sub(r"\1[삭제]", text)
    text = _PUSH_ENDPOINT.sub(r"\1[삭제]", text)
    return _BOT_TOKEN.sub("bot[삭제]", _EMAIL.sub("[이메일]", text))


def scrub_event(event: dict, hint: dict | None = None) -> dict:
    """Sentry before_send: 개인정보와 비밀값을 지운다."""
    request = event.get("request") or {}
    for key in _DROP_REQUEST_KEYS:
        request.pop(key, None)
    headers = request.get("headers") or {}
    for name in list(headers):
        if _SECRET_HEADER.search(name):
            headers[name] = "[삭제]"
    event.pop("user", None)
    for exc in (event.get("exception") or {}).get("values", []):
        if isinstance(exc.get("value"), str):
            exc["value"] = _scrub_text(exc["value"])
        # 지역 변수에는 질문, 근거 원문, 비밀번호가 들어 있을 수 있다
        for frame in (exc.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
    for crumb in (event.get("breadcrumbs") or {}).get("values", []):
        if isinstance(crumb.get("message"), str):
            crumb["message"] = _scrub_text(crumb["message"])
        crumb.pop("data", None)
    if isinstance(event.get("message"), str):
        event["message"] = _scrub_text(event["message"])
    if isinstance(event.get("logentry"), dict):
        msg = event["logentry"].get("message")
        if isinstance(msg, str):
            event["logentry"]["message"] = _scrub_text(msg)
        event["logentry"].pop("params", None)
    return event


def init_sentry(settings, component: str) -> bool:
    """component: api / worker / cli. 켰으면 True."""
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning("SENTRY_DSN 이 있지만 sentry-sdk 가 없습니다: pip install -e '.[ops]'")
        return False
    from dartrag import __version__

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=f"dartrag@{__version__}",
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=scrub_event,
        before_send_transaction=scrub_event,
        server_name=component,
    )
    sentry_sdk.set_tag("component", component)
    return True
