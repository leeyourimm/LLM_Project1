"""가입 없이 체험하기 (ALLOW_GUEST=true, 로그인을 켰을 때만).

체험 계정은 이메일·비밀번호 없이 만들고 GUEST_HOURS 가 지나면 기록과 함께 지운다.
질문, 대화 기록, 회사 대시보드, 기업 비교, 리포트, 공시 피드, 관심 종목 목록은 회원처럼 쓴다.

이메일이 있어야 하거나 오래 남는 계정이어야 뜻이 있는 기능은 아래 GUEST_BLOCKED 한 곳에 모아
막는다. 로그인 확인(app.py 의 current_user)이 라우트 경로를 보고 403 과 안내 문구를 돌려준다.
"""

from fastapi import Request

# (메서드, 라우트 경로) → 화면에 보일 기능 이름. 경로는 FastAPI 라우트에 적은 그대로 ({kind} 포함)
GUEST_BLOCKED: dict[tuple[str, str], str] = {
    # 공시 알림: 받을 곳(이메일·텔레그램·브라우저)을 계정에 묶어 두고 계속 보내는 기능.
    # 관심 종목 목록은 쓸 수 있지만 알림은 오지 않는다
    ("GET", "/api/alerts"): "공시 알림",
    ("POST", "/api/alerts/email"): "이메일 알림",
    ("POST", "/api/alerts/telegram"): "텔레그램 알림",
    ("PATCH", "/api/alerts/{kind}"): "공시 알림",
    ("DELETE", "/api/alerts/{kind}"): "공시 알림",
    ("POST", "/api/alerts/push"): "웹 푸시 알림",
    ("POST", "/api/alerts/push/test"): "웹 푸시 알림",
    ("DELETE", "/api/alerts/push/device"): "웹 푸시 알림",
    ("DELETE", "/api/alerts/push/devices/{device_id}"): "웹 푸시 알림",
    # 계정 보안: 이메일·비밀번호로 다시 로그인하는 계정에만 뜻이 있다
    ("POST", "/api/auth/password"): "비밀번호 변경",
    ("POST", "/api/auth/verify-email/resend"): "이메일 인증",
    ("GET", "/api/auth/2fa"): "2단계 인증",
    ("POST", "/api/auth/2fa/setup"): "2단계 인증",
    ("POST", "/api/auth/2fa/enable"): "2단계 인증",
    ("DELETE", "/api/auth/2fa"): "2단계 인증",
    ("POST", "/api/auth/2fa/recovery-codes"): "2단계 인증",
    # 계정 데이터: 체험 기록은 로그아웃하거나 기한이 지나면 바로 지워진다
    ("GET", "/api/account/export"): "내 데이터 내려받기",
    ("DELETE", "/api/account"): "계정 삭제",
}

# 계정 삭제는 가입을 권할 일이 아니라 체험을 끝내는 방법을 알려 준다
ENDING = "체험 계정은 로그아웃(체험 끝내기)하면 기록이 바로 지워집니다"


def blocked_detail(request: Request, allow_signup: bool) -> str | None:
    """체험 계정이 이 요청을 쓸 수 없으면 안내 문구, 쓸 수 있으면 None."""
    route = request.scope.get("route")
    feature = GUEST_BLOCKED.get((request.method, getattr(route, "path", None)))
    if feature is None:
        return None
    if feature == "계정 삭제":
        return f"{ENDING}. 따로 지울 필요가 없습니다."
    how = "가입하면" if allow_signup else "가입한 계정으로 로그인하면"
    return f"체험 계정에서는 {feature} 기능을 쓸 수 없습니다. {how} 쓸 수 있습니다."
