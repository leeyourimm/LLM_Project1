"""이메일·텔레그램·웹 푸시 발송. 비밀번호, 봇 토큰, 받는 주소, 푸시 구독 주소는 로그와 오류
메시지에 남기지 않는다."""

import json
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_LIMIT = 4096


class SendError(RuntimeError):
    pass


class PushGone(SendError):
    """푸시 서비스가 구독이 끝났다고 알렸다 (404·410). 그 구독은 지운다."""


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int = 587,
        *,
        username: str = "",
        password: str = "",
        sender: str,
        sender_name: str = "DART 공시 알림",
        starttls: bool = True,
        timeout: float = 20,
        smtp_factory=None,
    ):
        self.host, self.port = host, port
        self.username, self._password = username, password
        self.sender, self.sender_name = sender, sender_name
        self.starttls = starttls
        self.timeout = timeout
        self._smtp = smtp_factory or (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)

    def build(
        self, to: str, subject: str, text: str, unsubscribe_url: str | None = None
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self.sender_name, self.sender))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self.sender.rpartition("@")[2] or None)
        body = text
        if unsubscribe_url:
            # 메일 앱의 "구독 취소" 버튼이 쓰는 헤더 (RFC 8058)
            msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
            body += f"\n\n알림 이메일 그만 받기: {unsubscribe_url}"
        msg.set_content(body)
        return msg

    def send(self, to: str, subject: str, text: str, unsubscribe_url: str | None = None) -> None:
        msg = self.build(to, subject, text, unsubscribe_url)
        try:
            kwargs = {"timeout": self.timeout}
            if self._smtp is smtplib.SMTP_SSL:
                kwargs["context"] = ssl.create_default_context()
            with self._smtp(self.host, self.port, **kwargs) as smtp:
                if self.starttls and self._smtp is not smtplib.SMTP_SSL:
                    smtp.starttls(context=ssl.create_default_context())
                if self.username:
                    smtp.login(self.username, self._password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"이메일 전송 실패: {type(e).__name__}") from None


class TelegramSender:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self._token = token
        self._client = client or httpx.Client(base_url=TELEGRAM_API, timeout=15)

    def _call(self, method: str, payload: dict, timeout: float | None = None) -> dict:
        extra = {"timeout": timeout} if timeout else {}
        try:
            resp = self._client.post(f"/bot{self._token}/{method}", json=payload, **extra)
        except httpx.HTTPError as e:
            raise SendError(f"텔레그램 요청 실패: {type(e).__name__}") from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or not data.get("ok"):
            # 응답 설명은 짧게만 (토큰이 들어갈 일은 없지만 URL 은 남기지 않는다)
            desc = str(data.get("description", ""))[:120]
            raise SendError(f"텔레그램 전송 실패: HTTP {resp.status_code} {desc}".strip())
        return data

    def send(self, chat_id: str, text: str) -> None:
        if len(text) > TELEGRAM_LIMIT:
            text = text[: TELEGRAM_LIMIT - 1] + "…"
        self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout + 10).get("result", [])

    def set_webhook(self, url: str, secret: str) -> None:
        self._call(
            "setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message"]}
        )


class WebPushSender:
    """웹 푸시 (VAPID). 구독마다 내용을 암호화해 브라우저 회사의 푸시 서비스로 보낸다.

    구독 주소(endpoint)는 그 브라우저를 가리키는 개인 식별자라서 오류 메시지에 넣지 않는다.
    리디렉션은 따라가지 않는다 (httpx 기본값)."""

    def __init__(
        self,
        private_key: str,
        public_key: str,
        subject: str,
        client: httpx.Client | None = None,
        ttl: int = 86400,
    ):
        from dartrag.feed import webpush

        self._key = webpush.load_private_key(private_key)
        if webpush.public_key_of(self._key) != public_key.strip():
            raise webpush.PushKeyError("VAPID_PUBLIC_KEY 가 VAPID_PRIVATE_KEY 와 짝이 아닙니다")
        self.public_key = public_key.strip()
        self.subject = subject
        self.ttl = ttl  # 브라우저가 꺼져 있을 때 푸시 서비스가 보관할 시간(초)
        self._client = client or httpx.Client(timeout=15)

    def send(self, subscription: dict, message: dict) -> None:
        """subscription: endpoint, p256dh, auth. message: title, body, url (짧게, 비밀값 없이)."""
        from dartrag.feed import webpush

        endpoint = subscription["endpoint"]
        try:
            webpush.check_endpoint(endpoint)
            body = webpush.encrypt(
                json.dumps(message, ensure_ascii=False).encode(),
                subscription["p256dh"],
                subscription["auth"],
            )
        except webpush.PushKeyError as e:
            # 저장된 구독이 지금 규칙에 맞지 않으면 다시 쓸 수 없다
            raise PushGone(f"웹 푸시 구독을 쓸 수 없음: {e}") from None
        headers = {
            "Authorization": webpush.vapid_authorization(endpoint, self._key, self.subject),
            "TTL": str(self.ttl),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "Urgency": "normal",
        }
        try:
            resp = self._client.post(endpoint, content=body, headers=headers)
        except httpx.HTTPError as e:
            raise SendError(f"웹 푸시 요청 실패: {type(e).__name__}") from None
        if resp.status_code in (404, 410):
            raise PushGone(f"웹 푸시 구독 만료: HTTP {resp.status_code}")
        if not resp.is_success:
            # 리디렉션(3xx)도 실패로 본다. 응답 본문과 주소는 남기지 않는다
            raise SendError(f"웹 푸시 전송 실패: HTTP {resp.status_code}")
