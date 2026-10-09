"""로그인에 필요한 것들: 비밀번호 해시, 세션 토큰, 로그인 시도 제한.

외부 라이브러리 없이 표준 라이브러리(scrypt, secrets)만 쓴다.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque

SESSION_COOKIE = "dartrag_session"
PASSWORD_MIN = 10
PASSWORD_MAX = 128
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

# scrypt 매개변수 (메모리 16MB). 바꿔도 저장된 해시에 값이 들어 있어 예전 해시를 검증할 수 있다
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


class AuthError(ValueError):
    pass


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError("이메일 형식이 올바르지 않습니다")
    return email


def check_password_policy(password: str) -> None:
    if len(password) < PASSWORD_MIN:
        raise AuthError(f"비밀번호는 {PASSWORD_MIN}자 이상이어야 합니다")
    if len(password) > PASSWORD_MAX:
        raise AuthError(f"비밀번호는 {PASSWORD_MAX}자 이하여야 합니다")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


# 없는 이메일로 로그인할 때도 같은 시간이 걸리게 해서 가입 여부가 드러나지 않게 한다
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_session_token() -> tuple[str, str]:
    """(쿠키에 넣을 토큰, DB에 넣을 해시)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """실패한 로그인 시도를 세서 짧은 시간에 너무 많으면 막는다 (서버 한 대 기준, 메모리)."""

    def __init__(self, max_failures: int = 5, window: float = 900, clock=time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self.clock = clock
        self._fails: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _keys(self, ip: str, email: str) -> tuple[str, str]:
        # 같은 계정을 노리는 시도와, 한 곳에서 여러 계정을 노리는 시도를 따로 센다
        return f"acct:{email}", f"ip:{ip}"

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def blocked(self, ip: str, email: str) -> bool:
        now = self.clock()
        acct, addr = self._keys(ip, email)
        with self._lock:
            for key, limit in ((acct, self.max_failures), (addr, self.max_failures * 4)):
                q = self._fails[key]
                self._trim(q, now)
                if len(q) >= limit:
                    return True
        return False

    def failed(self, ip: str, email: str) -> None:
        now = self.clock()
        with self._lock:
            for key in self._keys(ip, email):
                self._fails[key].append(now)

    def succeeded(self, ip: str, email: str) -> None:
        with self._lock:
            self._fails.pop(self._keys(ip, email)[0], None)
