"""웹 푸시 (RFC 8030): 내용 암호화(RFC 8291, aes128gcm)와 서버 인증(VAPID, RFC 8292).

웹 푸시 라이브러리(pywebpush 등) 대신 cryptography 의 기본 연산(ECDH P-256, HKDF-SHA256,
AES-128-GCM, ECDSA P-256)으로 규격의 틀만 짰다. 보내기는 다른 채널처럼 httpx 로 한다
(channels.WebPushSender). RFC 8291 부록 A 의 예시 값으로 테스트한다.

구독 주소(endpoint)와 브라우저 키는 사용자 브라우저가 보낸 값이다. 서버가 그 주소로 요청을 보내므로
알려진 브라우저 푸시 서비스의 https 주소만 받는다 (내부망 주소로 요청하게 만들지 못하게).
"""

import base64
import json
import os
import struct
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

RECORD_SIZE = 4096
# 푸시 서비스는 본문을 4096바이트까지 받는다.
# 머리(86바이트), 태그(16), 구분 바이트(1)를 빼고 여유를 둔다
MAX_PLAINTEXT = 3000
# 브라우저 푸시 서비스 (Chrome·Edge·삼성 인터넷·Whale 은 FCM, Firefox 는 Mozilla, Safari 는 Apple)
PUSH_HOSTS = frozenset(
    {
        "fcm.googleapis.com",
        "android.googleapis.com",
        "updates.push.services.mozilla.com",
        "push.services.mozilla.com",
        "web.push.apple.com",
    }
)
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com")
MAX_ENDPOINT = 2048


class PushKeyError(ValueError):
    """구독 정보나 VAPID 키가 올바르지 않다. 메시지에 키 값을 넣지 않는다."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    text = (text or "").strip()
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        raise PushKeyError("base64url 형식이 아닙니다") from None


# --- VAPID 키 (서버 키) --------------------------------------------------------


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def generate_vapid_keys() -> tuple[str, str]:
    """(비밀키, 공개키). 비밀키는 32바이트 값, 공개키는 65바이트 점을 base64url 로
    (web-push 관례)."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url(private), b64url(_raw_public(key.public_key()))


def load_private_key(text: str) -> ec.EllipticCurvePrivateKey:
    raw = b64url_decode(text)
    if len(raw) != 32:
        raise PushKeyError(
            "VAPID_PRIVATE_KEY 형식이 올바르지 않습니다 (dartrag push keys 로 만드세요)"
        )
    try:
        return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    except ValueError:
        raise PushKeyError("VAPID_PRIVATE_KEY 형식이 올바르지 않습니다") from None


def public_key_of(key: ec.EllipticCurvePrivateKey) -> str:
    return b64url(_raw_public(key.public_key()))


# --- 구독 정보 검사 -------------------------------------------------------------


def push_host(endpoint: str) -> str:
    """구독 주소의 호스트 (내보내기·화면 표시용). 주소 나머지는 비밀처럼 다룬다."""
    return (urlsplit(endpoint).hostname or "").lower()


def check_endpoint(endpoint: str) -> str:
    """알려진 푸시 서비스의 https 주소인지. 아니면 PushKeyError."""
    if not endpoint or len(endpoint) > MAX_ENDPOINT:
        raise PushKeyError("구독 주소가 올바르지 않습니다")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise PushKeyError("구독 주소가 올바르지 않습니다") from None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not (host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES))
        or any(c.isspace() for c in endpoint)
    ):
        raise PushKeyError("지원하지 않는 푸시 서비스 주소입니다")
    return endpoint


def check_subscription_keys(p256dh: str, auth: str) -> None:
    """브라우저 공개키(P-256 점 65바이트)와 인증 비밀값(16바이트)."""
    raw = b64url_decode(p256dh)
    if len(raw) != 65 or raw[0] != 4:
        raise PushKeyError("구독 키(p256dh)가 올바르지 않습니다")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError:
        raise PushKeyError("구독 키(p256dh)가 올바르지 않습니다") from None
    if len(b64url_decode(auth)) != 16:
        raise PushKeyError("구독 키(auth)가 올바르지 않습니다")


# --- 내용 암호화 (RFC 8291 + RFC 8188 aes128gcm) --------------------------------


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(hashes.SHA256(), length, salt=salt, info=info).derive(ikm)


def encrypt(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    server_key: ec.EllipticCurvePrivateKey | None = None,
    record_size: int = RECORD_SIZE,
) -> bytes:
    """브라우저만 풀 수 있게 암호화한 요청 본문. salt·server_key 는 테스트에서만 넘긴다
    (보낼 때마다 새로 만들어야 한다)."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise PushKeyError("알림 내용이 너무 깁니다")
    ua_raw = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_raw)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_raw = _raw_public(server_key.public_key())
    salt = salt or os.urandom(16)

    shared = server_key.exchange(ec.ECDH(), ua_public)
    # RFC 8291 3.4: 인증 비밀값과 두 공개키를 섞어 입력 키를 만든다
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_raw + as_raw, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    # 레코드 하나 (마지막 레코드 구분 바이트 0x02, 덧붙임 없음)
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack("!I", record_size) + bytes([len(as_raw)]) + as_raw
    return header + ciphertext


# --- VAPID (RFC 8292) -----------------------------------------------------------


def vapid_authorization(
    endpoint: str,
    key: ec.EllipticCurvePrivateKey,
    subject: str,
    *,
    now: float | None = None,
    lifetime: int = 12 * 3600,
) -> str:
    """Authorization 헤더 값. JWT(ES256)의 aud 는 푸시 서비스 출처, 유효 기간은 24시간 이하."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(now if now is not None else time.time()) + lifetime,
        "sub": subject,
    }

    def enc(obj: dict) -> str:
        return b64url(json.dumps(obj, separators=(",", ":")).encode())

    signing_input = f"{enc({'typ': 'JWT', 'alg': 'ES256'})}.{enc(claims)}"
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={signing_input}.{signature}, k={public_key_of(key)}"
