"""2단계 인증 API (계정 화면): 상태, 등록 시작(QR·직접 입력 키), 첫 코드 확인, 끄기,
복구 코드 새로 받기.

로그인할 때 코드를 받는 단계(/api/auth/login/2fa)와 비밀번호 재설정 뒤의 처리는 app.py 에 있다.
비밀값과 복구 코드 원문은 응답으로 한 번만 보내고 로그에 남기지 않는다.
"""

import logging

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from dartrag.web import auth, totp

log = logging.getLogger(__name__)

CHALLENGE_COOKIE = "dartrag_2fa"
CHALLENGE_MINUTES = 5
CHALLENGE_ATTEMPTS = 5
CODE_MAX = 40  # 6자리 코드 또는 복구 코드(하이픈·공백 포함)

WRONG_CODE = "인증 코드가 맞지 않습니다"
WRONG_PASSWORD = "비밀번호가 맞지 않습니다"


class PasswordOnly(BaseModel):
    password: str = Field(max_length=auth.PASSWORD_MAX)


class CodeOnly(BaseModel):
    code: str = Field(min_length=1, max_length=CODE_MAX)


class PasswordAndCode(BaseModel):
    password: str = Field(max_length=auth.PASSWORD_MAX)
    code: str = Field(min_length=1, max_length=CODE_MAX)


def check_second_factor(services, repo, user_id: int, code: str) -> dict | None:
    """인증 앱 코드(6자리)나 복구 코드가 맞으면 {"method": …, "recovery_codes_left": …}.

    쓴 코드는 다시 쓸 수 없다: 인증 앱 코드는 그 시간 구간을, 복구 코드는 그 코드를 지운다."""
    state = repo.totp(user_id)
    if state is None or not state["enabled"]:
        return None
    if totp.looks_like_totp(code):
        secret = totp.unseal(services.secret_key, user_id, state["secret"])
        if secret is None:
            # SECRET_KEY 가 바뀌어 비밀값을 풀 수 없다. 복구 코드로만 들어올 수 있다
            log.warning("2단계 인증 비밀값을 풀 수 없음 (SECRET_KEY 변경?)")
            return None
        step = totp.match_step(secret, code, last_step=state["last_step"])
        if step is None or not repo.use_totp_step(user_id, step):
            return None
        return {"method": "totp", "recovery_codes_left": state["recovery_left"]}
    left = repo.use_recovery_code(user_id, totp.recovery_hash(user_id, code))
    if left is None:
        return None
    return {"method": "recovery", "recovery_codes_left": left}


def build_router(services, CurrentUser, rate, start_session) -> APIRouter:  # noqa: N803
    """start_session(response, user_id): 켤 때 다른 기기 로그인을 끊고 이 기기는 새 세션으로."""
    api = APIRouter()

    def need_user(user):
        if user is None:
            raise HTTPException(400, "로그인을 쓰지 않는 설정입니다 (AUTH_REQUIRED=false)")
        return user

    def check_password(repo, user, password: str) -> None:
        row = repo.user_by_email(user.email)
        if row is None or not auth.verify_password(password, row[2]):
            raise HTTPException(401, WRONG_PASSWORD)

    @api.get("/api/auth/2fa")
    def status(user: CurrentUser = None):
        user = need_user(user)
        with services.repo() as repo:
            state = repo.totp(user.id)
        enabled = bool(state and state["enabled"])
        return {
            "enabled": enabled,
            "recovery_codes_left": state["recovery_left"] if enabled else 0,
        }

    @api.post("/api/auth/2fa/setup", dependencies=[rate("auth")])
    def setup(req: PasswordOnly, user: CurrentUser = None):
        """등록 시작. 세션을 훔친 사람이 자기 인증 앱을 걸어 주인을 막지 못하게 비밀번호를 확인한다.

        비밀값은 첫 코드를 확인하기 전까지는 로그인에 쓰지 않는다."""
        user = need_user(user)
        secret = totp.new_secret()
        with services.repo() as repo:
            check_password(repo, user, req.password)
            if not repo.start_totp(user.id, totp.seal(services.secret_key, user.id, secret)):
                raise HTTPException(409, "2단계 인증이 이미 켜져 있습니다")
        uri = totp.otpauth_uri(secret, user.email)
        return {
            "secret": totp.group_secret(secret),
            "otpauth_uri": uri,
            "qr": totp.qr_data_uri(uri),
            "issuer": totp.ISSUER,
            "account": user.email,
        }

    @api.post("/api/auth/2fa/enable", dependencies=[rate("auth")])
    def enable(req: CodeOnly, response: Response, user: CurrentUser = None):
        """앱에 뜬 첫 코드를 확인하면 켜고 복구 코드를 한 번만 보여 준다.

        다른 기기의 로그인은 모두 끊는다 (의심스러워서 켜는 경우가 많으므로)."""
        user = need_user(user)
        with services.repo() as repo:
            state = repo.totp(user.id)
            if state is None or state["enabled"]:
                raise HTTPException(400, "등록을 먼저 시작해 주세요")
            secret = totp.unseal(services.secret_key, user.id, state["secret"])
            step = totp.match_step(secret, req.code) if secret else None
            if step is None:
                raise HTTPException(
                    400, f"{WRONG_CODE}. 휴대폰 시계가 자동으로 맞춰져 있는지 확인해 주세요"
                )
            codes = totp.new_recovery_codes()
            hashes = [totp.recovery_hash(user.id, c) for c in codes]
            if not repo.enable_totp(user.id, step, hashes):
                raise HTTPException(400, "등록을 먼저 시작해 주세요")
        start_session(response, user.id)
        return {"enabled": True, "recovery_codes": codes}

    @api.delete("/api/auth/2fa", dependencies=[rate("auth")])
    def disable(req: PasswordAndCode, user: CurrentUser = None):
        """끄기: 비밀번호와 인증 코드(또는 복구 코드)를 모두 확인한다."""
        user = need_user(user)
        with services.repo() as repo:
            check_password(repo, user, req.password)
            if check_second_factor(services, repo, user.id, req.code) is None:
                raise HTTPException(401, WRONG_CODE)
            repo.disable_totp(user.id)
        return {"enabled": False}

    @api.post("/api/auth/2fa/recovery-codes", dependencies=[rate("auth")])
    def regenerate(req: PasswordAndCode, user: CurrentUser = None):
        """복구 코드 새로 받기. 남은 예전 코드는 모두 못 쓰게 된다."""
        user = need_user(user)
        with services.repo() as repo:
            check_password(repo, user, req.password)
            if check_second_factor(services, repo, user.id, req.code) is None:
                raise HTTPException(401, WRONG_CODE)
            codes = totp.new_recovery_codes()
            repo.replace_recovery_codes(user.id, [totp.recovery_hash(user.id, c) for c in codes])
        return {"recovery_codes": codes}

    return api
