"""계정 메일 문구: 비밀번호 재설정, 이메일 인증, 새 기기 로그인 알림.

링크에는 한 번 쓰는 토큰이 들어 있으므로 이 문구를 로그에 남기지 않는다.
링크는 웹 화면(Next.js) 주소로 보낸다. 화면이 토큰을 POST 로 API 에 넘긴다.
"""

from datetime import UTC, datetime, timedelta, timezone

RESET_MINUTES = 30
VERIFY_HOURS = 24
KST = timezone(timedelta(hours=9), "KST")

# 가입 여부와 관계없이 같은 문구를 돌려준다
FORGOT_MESSAGE = (
    "입력한 주소로 가입된 계정이 있으면 비밀번호 재설정 링크를 보냈습니다. "
    f"링크는 {RESET_MINUTES}분 동안 한 번만 쓸 수 있습니다. "
    "메일이 안 보이면 스팸함도 확인해 주세요."
)
NO_MAIL = (
    "이 서버에는 메일 발송(SMTP)이 설정되지 않아 메일로 비밀번호를 재설정할 수 없습니다. "
    "서비스 운영자에게 비밀번호 변경을 요청해 주세요."
)


def _base(public_url: str) -> str:
    return public_url.rstrip("/")


def reset_mail(public_url: str, token: str) -> tuple[str, str]:
    link = f"{_base(public_url)}/reset-password?token={token}"
    body = (
        "DART 공시 분석 비밀번호 재설정을 요청하셨습니다.\n"
        "아래 링크를 열어 새 비밀번호를 정해 주세요.\n\n"
        f"{link}\n\n"
        f"링크는 {RESET_MINUTES}분 동안 한 번만 쓸 수 있습니다. 비밀번호를 바꾸면 "
        "모든 기기에서 로그아웃됩니다.\n"
        "직접 요청하지 않았다면 이 메일을 무시하세요. 비밀번호는 바뀌지 않습니다."
    )
    return "[DART 공시 분석] 비밀번호 재설정", body


def verify_mail(public_url: str, token: str) -> tuple[str, str]:
    link = f"{_base(public_url)}/verify-email?token={token}"
    body = (
        "DART 공시 분석에 가입한 이메일 주소를 확인합니다.\n"
        "아래 링크를 열면 인증이 끝납니다.\n\n"
        f"{link}\n\n"
        f"링크는 {VERIFY_HOURS}시간 동안 유효합니다. 직접 가입하지 않았다면 이 메일을 무시하세요."
    )
    return "[DART 공시 분석] 이메일 인증", body


def new_device_mail(
    public_url: str, browser: str, system: str, network: str, when: datetime | None = None
) -> tuple[str, str]:
    when = (when or datetime.now(UTC)).astimezone(KST)
    body = (
        "처음 보는 기기에서 DART 공시 분석 계정에 로그인했습니다.\n\n"
        f"시각: {when:%Y-%m-%d %H:%M} (한국 시간)\n"
        f"브라우저: {browser} · {system}\n"
        f"접속 네트워크: {network}\n\n"
        "본인이라면 따로 할 일은 없습니다.\n"
        "본인이 아니라면 바로 비밀번호를 바꾸세요. 바꾸면 모든 기기에서 로그아웃됩니다.\n"
        f"{_base(public_url)}/forgot-password"
    )
    return "[DART 공시 분석] 새 기기 로그인 알림", body
