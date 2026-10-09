"""텔레그램 봇: 사용자가 웹에서 받은 연결 링크로 봇을 시작하면 그 대화방을 알림 채널로 등록한다.

공개 서버는 웹훅(/api/telegram/webhook), 내 컴퓨터에서는 `dartrag telegram poll` 로 받는다.
"""

import hashlib
import logging
import secrets

log = logging.getLogger(__name__)

HELP = (
    "DART 공시 알림 봇입니다.\n"
    "웹 화면의 알림 설정에서 '텔레그램 연결'을 누르면\n"
    "이 대화방으로 관심 종목 공시를 보내 드립니다.\n"
    "/stop 알림 멈추기"
)


def token_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_link_code() -> tuple[str, str]:
    """(사용자에게 줄 코드, DB 에 저장할 해시). 텔레그램 start 값은 64자 이하 영숫자·_-."""
    code = secrets.token_urlsafe(24)
    return code, token_hash(code)


def handle_update(repo, update: dict, sender) -> str | None:
    """업데이트 하나를 처리하고 보낸 답장을 돌려준다."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    text = (msg.get("text") or "").strip()
    if not text or chat.get("id") is None:
        return None
    chat_id = str(chat["id"])
    if chat.get("type") != "private":
        reply = "알림은 봇과의 1:1 대화에서만 연결할 수 있습니다."
    elif text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            reply = HELP
        else:
            user_id = repo.confirm_alert_channel("telegram", token_hash(parts[1]), target=chat_id)
            reply = (
                "연결됐습니다. 관심 종목의 주요 공시를 이 대화방으로 보내 드립니다.\n"
                "/stop 알림 멈추기"
                if user_id
                else "연결 코드가 맞지 않거나 만료됐습니다. 웹 화면에서 다시 연결해 주세요."
            )
    elif text.startswith("/stop"):
        n = repo.disable_telegram_chat(chat_id)
        reply = "알림을 멈췄습니다. 웹 화면에서 다시 켤 수 있습니다." if n else HELP
    else:
        reply = HELP
    try:
        sender.send(chat_id, reply)
    except Exception as e:  # noqa: BLE001
        log.warning("텔레그램 답장 실패: %s", e)
    return reply
