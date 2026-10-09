"""사용자별 공시 알림 (이메일·텔레그램·웹 푸시).

한 번 돌 때 사용자·채널마다 모인 공시를 한 통으로 묶어 보낸다.
정기보고서에 변경점 요약이 있으면 핵심 문장을 함께 넣는다 (웹 푸시는 제목·요약·링크만).
"""

import hashlib
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import groupby

from dartrag.feed.channels import PushGone, SendError
from dartrag.feed.notify import IMPORTANCE_MARK

log = logging.getLogger(__name__)

DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
MAX_ITEMS = 10
PUSH_ITEMS = 3  # 웹 푸시 알림에 줄로 적는 공시 수
PUSH_FEED = "/feed"  # 여러 건일 때 알림을 누르면 여는 화면 (웹 화면 안의 경로)


@dataclass
class AlertRun:
    sent: int = 0  # 보낸 메시지(묶음) 수
    items: int = 0  # 그 안에 든 공시 수
    errors: list[str] = field(default_factory=list)


def _headline(repo, rcept_no: str, limit: int = 3) -> list[str]:
    payload = repo.latest_diff_summary_for(rcept_no)
    if not payload:
        return []
    from dartrag.changes.summary import DiffDigest

    try:
        return DiffDigest.from_dict(payload).headline(limit)
    except (KeyError, TypeError):  # 예전 형식으로 저장된 요약
        return []


def format_item(d: dict, headline: list[str]) -> str:
    corr = " (정정)" if d.get("correction") else ""
    lines = [
        f"{IMPORTANCE_MARK.get(d['importance'], '')} {d['corp_name']} · {d['event_label']}{corr}",
        f"{d['report_nm']} ({d['rcept_dt']})",
    ]
    if headline:
        lines.append("주요 변경점:")
        lines += [f"  - {h}" for h in headline]
    lines.append(DART_URL.format(d["rcept_no"]))
    return "\n".join(lines)


def _who(items: list[dict]) -> str:
    names = list(dict.fromkeys(d["corp_name"] for d in items))
    return names[0] + (f" 외 {len(names) - 1}곳" if len(names) > 1 else "")


def format_bundle(items: list[dict], headlines: dict[str, list[str]]) -> tuple[str, str]:
    """(제목, 본문). 많으면 중요한 것부터 MAX_ITEMS 개만 넣고 나머지 수를 적는다."""
    shown = items[:MAX_ITEMS]
    subject = f"[DART 알림] {_who(items)} 공시 {len(items)}건"
    body = "\n\n".join(format_item(d, headlines.get(d["rcept_no"], [])) for d in shown)
    if len(items) > len(shown):
        body += f"\n\n… 외 {len(items) - len(shown)}건은 웹 화면의 공시 피드에서 확인하세요."
    body += "\n\n공시 정보 알림이며 투자 권유가 아닙니다."
    return subject, body


def format_push(items: list[dict]) -> dict:
    """웹 푸시 알림: 제목, 짧은 요약, 누르면 열 주소. 같은 묶음을 메일·텔레그램보다 짧게 쓴다.

    알림은 잠금 화면에도 보이고 푸시 서비스를 거치므로 구독 취소 토큰 같은 비밀값은 넣지 않는다.
    한 건이면 DART 원문, 여러 건이면 웹 화면의 공시 피드를 연다."""
    lines = []
    for d in items[:PUSH_ITEMS]:
        corr = " (정정)" if d.get("correction") else ""
        mark = IMPORTANCE_MARK.get(d["importance"], "")
        lines.append(f"{mark} {d['corp_name']} · {d['event_label']}{corr}".strip())
    if len(items) > PUSH_ITEMS:
        lines.append(f"… 외 {len(items) - PUSH_ITEMS}건")
    url = DART_URL.format(items[0]["rcept_no"]) if len(items) == 1 else PUSH_FEED
    return {
        "title": f"{_who(items)} 공시 {len(items)}건"[:80],
        "body": "\n".join(lines)[:300],
        "url": url,
    }


def send_push(repo, sender, user_id: int, message: dict) -> int:
    """사용자가 구독한 모든 브라우저로 보내고 보낸 곳 수를 돌려준다.

    푸시 서비스가 끝났다고 한 구독(404·410)과 예전 서버 키(VAPID)로 만든 구독은 지운다.
    한 곳에도 보내지 못했으면 SendError (다음 번에 다시 보낸다)."""
    sent, gone, failed = [], [], []
    for sub in repo.push_subscriptions(user_id):
        if sub["vapid_key"] != sender.public_key:
            gone.append(sub["id"])  # 서버 키를 바꾸면 예전 구독으로는 보낼 수 없다
            continue
        try:
            sender.send(sub, message)
        except PushGone:
            gone.append(sub["id"])
        except SendError as e:
            failed.append(str(e))
        else:
            sent.append(sub["id"])
    repo.record_push_results(user_id, sent, gone)
    if not sent:
        raise SendError(failed[0] if failed else "웹 푸시: 알림을 받을 브라우저가 없습니다")
    return len(sent)


def unsubscribe_token(secret: str, user_id: int, kind: str) -> str:
    msg = f"unsubscribe:{user_id}:{kind}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def check_unsubscribe(secret: str, user_id: int, kind: str, token: str) -> bool:
    return bool(secret) and hmac.compare_digest(unsubscribe_token(secret, user_id, kind), token)


def unsubscribe_link(public_url: str, secret: str) -> Callable[[int, str], str | None]:
    """메일 속 '그만 받기' 링크. 비밀값이 없으면 링크를 넣지 않는다."""

    def make(user_id: int, kind: str) -> str | None:
        if not secret:
            return None
        token = unsubscribe_token(secret, user_id, kind)
        return f"{public_url.rstrip('/')}/api/alerts/unsubscribe?u={user_id}&k={kind}&t={token}"

    return make


def send_user_alerts(
    repo,
    senders: dict[str, object],
    *,
    unsubscribe_url: Callable[[int, str], str | None] = lambda uid, kind: None,
) -> AlertRun:
    """senders: {"email": EmailSender, "telegram": TelegramSender, "push": WebPushSender}.
    없는 채널은 건너뛴다."""
    run = AlertRun()
    rows = repo.user_pending_alerts()
    headlines: dict[str, list[str]] = {}
    for (user_id, kind, target), group in groupby(
        rows, key=lambda r: (r["user_id"], r["kind"], r["target"])
    ):
        sender = senders.get(kind)
        if sender is None:
            continue
        items = list(group)
        for d in items:
            if d["rcept_no"] not in headlines:
                headlines[d["rcept_no"]] = _headline(repo, d["rcept_no"])
        subject, body = format_bundle(items, headlines)
        try:
            if kind == "email":
                sender.send(target, subject, body, unsubscribe_url(user_id, kind))
            elif kind == "push":
                send_push(repo, sender, user_id, format_push(items))
            else:
                sender.send(target, f"{subject}\n\n{body}")
        except Exception as e:  # noqa: BLE001 - 한 사람 실패가 다른 사람 발송을 막지 않게
            log.warning("사용자 %s %s 알림 실패: %s", user_id, kind, e)
            run.errors.append(f"user {user_id} {kind}: {e}")
            continue
        # 묶음에 넣지 못한 나머지도 보낸 것으로 친다 (본문에 개수를 적었으므로)
        repo.mark_user_notified(user_id, [d["rcept_no"] for d in items], kind)
        run.sent += 1
        run.items += len(items)
    return run
