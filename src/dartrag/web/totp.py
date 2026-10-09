"""2단계 인증: TOTP (RFC 6238), 복구 코드, 비밀값 암호화.

인증 앱(Google Authenticator, Microsoft Authenticator, 1Password 등)과 맞추려고 SHA-1, 6자리,
30초를 쓴다. 코드 계산은 표준 라이브러리(hmac)만 쓴다.

서버는 코드를 계산하려고 비밀값 원문이 필요해서 해시로 둘 수 없다. 그래서 SECRET_KEY 로 만든 키로
암호화(AES-GCM)해 DB 에 둔다. SECRET_KEY 를 바꾸면 비밀값을 풀 수 없으므로 그 사용자는 복구 코드로
로그인해 2단계 인증을 다시 등록해야 한다.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

DIGITS = 6
PERIOD = 30
WINDOW = 1  # 휴대폰 시계가 조금 어긋나도 되게 앞뒤 한 구간(30초)씩 받는다
ISSUER = "DART 공시 분석"
RECOVERY_COUNT = 10
# 헷갈리는 글자(i l o 0 1)를 뺀 31자. 12자면 약 59비트라 해시가 새도 되찾기 어렵다
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
RECOVERY_GROUPS, RECOVERY_GROUP_LEN = 3, 4


# --- TOTP ---------------------------------------------------------------------


def new_secret() -> str:
    """새 비밀값 (160비트, RFC 4226 권장 길이). 인증 앱에 넣는 base32 문자열."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def secret_bytes(secret: str) -> bytes:
    s = "".join(secret.split()).upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS, algorithm: str = "sha1") -> str:
    """RFC 4226 HOTP (동적 잘라내기)."""
    mac = hmac.new(key, struct.pack(">Q", counter), algorithm).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def time_step(at: float | None = None, period: int = PERIOD) -> int:
    return int((time.time() if at is None else at) // period)


def totp(
    key: bytes,
    at: float | None = None,
    *,
    digits: int = DIGITS,
    period: int = PERIOD,
    algorithm: str = "sha1",
) -> str:
    """RFC 6238 TOTP. 테스트 값(부록 B)을 확인하려고 자릿수·주기·해시를 바꿀 수 있게 했다."""
    return hotp(key, time_step(at, period), digits, algorithm)


def normalize_code(code: str) -> str:
    """사용자가 넣은 코드에서 공백과 하이픈을 빼고 소문자로."""
    return "".join((code or "").split()).replace("-", "").lower()


def looks_like_totp(code: str) -> bool:
    c = normalize_code(code)
    return len(c) == DIGITS and c.isdigit()


def match_step(
    secret: str, code: str, at: float | None = None, last_step: int | None = None
) -> int | None:
    """맞는 코드면 그 코드의 시간 구간 번호, 아니면 None.

    이미 쓴 구간(last_step 이하)의 코드는 받지 않는다 (같은 코드 재사용 방지, RFC 6238 5.2)."""
    code = normalize_code(code)
    if not looks_like_totp(code):
        return None
    key = secret_bytes(secret)
    now = time_step(at)
    found = None
    for step in range(now - WINDOW, now + WINDOW + 1):
        # 맞는 것을 찾아도 끝까지 같은 계산을 한다 (응답 시간 차이를 줄이려고)
        ok = hmac.compare_digest(hotp(key, step), code)
        if ok and found is None and (last_step is None or step > last_step):
            found = step
    return found


def otpauth_uri(secret: str, account: str) -> str:
    """인증 앱 등록 주소 (QR 코드에 넣는다). Google Authenticator Key Uri Format."""
    label = quote(f"{ISSUER}:{account}", safe=":@")
    params = urlencode(
        {
            "secret": secret,
            "issuer": ISSUER,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": PERIOD,
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{params}"


def qr_data_uri(text: str) -> str:
    """QR 코드 SVG 를 data: 주소로. 화면은 <img> 로만 보여 준다 (외부 QR 서비스에 비밀값을
    보내지 않는다)."""
    import segno

    return segno.make(text, error="m").svg_data_uri(scale=5, border=2, dark="#000", light="#fff")


def group_secret(secret: str) -> str:
    """직접 입력하기 쉽게 4글자씩 띄운다."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


# --- 복구 코드 -------------------------------------------------------------------


def new_recovery_codes(count: int = RECOVERY_COUNT) -> list[str]:
    """예: 'k7mp-x2qa-r9td'. 원문은 사용자에게 한 번만 보여 주고 해시만 저장한다."""
    return [
        "-".join(
            "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP_LEN))
            for _ in range(RECOVERY_GROUPS)
        )
        for _ in range(count)
    ]


def recovery_hash(user_id: int, code: str) -> str:
    """사용자 번호를 섞은 SHA-256 (같은 코드라도 사용자마다 해시가 다르다)."""
    return hashlib.sha256(f"recovery:{user_id}:{normalize_code(code)}".encode()).hexdigest()


# --- 비밀값 암호화 ---------------------------------------------------------------


def _box_key(app_secret: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    material = (app_secret or "dartrag-totp").encode()
    return HKDF(hashes.SHA256(), 32, salt=None, info=b"dartrag totp secret v1").derive(material)


def seal(app_secret: str, user_id: int, secret: str) -> str:
    """DB 에 둘 암호문. 사용자 번호를 함께 묶어 다른 사용자 줄로 옮겨 쓰지 못하게 한다."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    data = AESGCM(_box_key(app_secret)).encrypt(nonce, secret.encode(), f"totp:{user_id}".encode())
    return "v1:" + base64.urlsafe_b64encode(nonce + data).decode()


def unseal(app_secret: str, user_id: int, sealed: str) -> str | None:
    """풀 수 없으면 None (SECRET_KEY 가 바뀌었거나 값이 망가짐)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not sealed.startswith("v1:"):
        return None
    try:
        raw = base64.urlsafe_b64decode(sealed[3:])
        plain = AESGCM(_box_key(app_secret)).decrypt(raw[:12], raw[12:], f"totp:{user_id}".encode())
    except (ValueError, InvalidTag):
        return None
    return plain.decode()
