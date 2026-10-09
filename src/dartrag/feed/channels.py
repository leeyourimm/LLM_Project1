"""이메일·텔레그램 발송. 비밀번호, 봇 토큰, 받는 주소는 로그와 오류 메시지에 남기지 않는다."""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_LIMIT = 4096


class SendError(RuntimeError):
    pass


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
