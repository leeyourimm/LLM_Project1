"""로그인에 필요한 것들: 비밀번호 해시, 세션 토큰, 로그인 시도 제한, 기기 구분.

외부 라이브러리 없이 표준 라이브러리(scrypt, secrets)만 쓴다.
"""

import base64
import hashlib
import hmac
import ipaddress
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


# 메일 링크(비밀번호 재설정, 이메일 인증)에 넣는 한 번 쓰는 토큰도 세션 토큰과 같은 방식:
# 원문은 메일에만, DB 에는 SHA-256 해시만
new_link_token = new_session_token


# --- 기기 구분 (새 기기 로그인 알림) -------------------------------------------
# 브라우저 버전이나 휴대폰 IP 끝자리는 자주 바뀌어서, 그대로 쓰면 같은 기기도 매번 "새 기기"가
# 된다. 브라우저 종류·OS·접속 네트워크(IPv4 /24, IPv6 /64)만 보고, 저장할 때는 해시만 남긴다.

_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser", "삼성 인터넷"),
    ("Whale/", "Whale"),
    ("CriOS", "Chrome"),
    ("FxiOS", "Firefox"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS = (
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("Android", "Android"),
    ("CrOS", "ChromeOS"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("Linux", "Linux"),
)


def device_label(user_agent: str | None) -> tuple[str, str]:
    """(브라우저, OS). 모르면 '알 수 없는 브라우저' 같은 값."""
    ua = user_agent or ""
    browser = next((name for key, name in _BROWSERS if key in ua), "알 수 없는 브라우저")
    system = next((name for key, name in _SYSTEMS if key in ua), "알 수 없는 OS")
    return browser, system


def network_of(ip: str | None) -> str:
    """접속 주소의 네트워크 대역. 예: 203.0.113.7 → 203.0.113.0/24."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return "알 수 없음"
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def device_hash(key: str, user_id: int, user_agent: str | None, ip: str | None) -> str:
    """기기 기록용 HMAC. IPv4 대역은 경우의 수가 적어 그냥 해시하면 되돌릴 수 있으므로
    SECRET_KEY 를 키로 쓴다 (없으면 고정 문자열, doctor 가 SECRET_KEY 를 경고한다)."""
    browser, system = device_label(user_agent)
    msg = f"{user_id}|{browser}|{system}|{network_of(ip)}".encode()
    return hmac.new((key or "dartrag-device").encode(), msg, hashlib.sha256).hexdigest()


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
