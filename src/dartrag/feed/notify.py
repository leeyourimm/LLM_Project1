"""알림 채널. 웹훅 주소는 비밀 정보라 로그에 남기지 않는다."""

from typing import Protocol

import httpx


class Notifier(Protocol):
    channel: str

    def send(self, text: str) -> None: ...


class ConsoleNotifier:
    channel = "console"

    def __init__(self, echo=print):
        self.echo = echo

    def send(self, text: str) -> None:
        self.echo(text)


class WebhookNotifier:
    """Slack·Discord 수신 웹훅. 주소를 보고 메시지 형식을 고른다."""

    channel = "webhook"

    def __init__(self, url: str, client: httpx.Client | None = None):
        self._url = url
        self._client = client or httpx.Client(timeout=10)
        self._key = "content" if "discord.com" in url or "discordapp.com" in url else "text"

    def send(self, text: str) -> None:
        try:
            resp = self._client.post(self._url, json={self._key: text})
        except httpx.HTTPError as e:
            raise RuntimeError(f"웹훅 전송 실패: {type(e).__name__}") from None
        if resp.status_code >= 400:
            # 응답 본문이나 주소를 그대로 노출하지 않는다
            raise RuntimeError(f"웹훅 전송 실패: HTTP {resp.status_code}")


IMPORTANCE_MARK = {3: "🔴", 2: "🟠", 1: "⚪"}


def format_alert(d: dict) -> str:
    corr = " (정정)" if d.get("correction") else ""
    return (
        f"{IMPORTANCE_MARK.get(d['importance'], '')} {d['corp_name']} · {d['event_label']}{corr}\n"
        f"{d['report_nm']} ({d['rcept_dt']})\n"
        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d['rcept_no']}"
    )
