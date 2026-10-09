"""사용자별 공시 알림 (이메일·텔레그램).

한 번 돌 때 사용자·채널마다 모인 공시를 한 통으로 묶어 보낸다.
정기보고서에 변경점 요약이 있으면 핵심 문장을 함께 넣는다.
"""

import hashlib
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import groupby

from dartrag.feed.notify import IMPORTANCE_MARK

log = logging.getLogger(__name__)

DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
MAX_ITEMS = 10


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


def format_bundle(items: list[dict], headlines: dict[str, list[str]]) -> tuple[str, str]:
    """(제목, 본문). 많으면 중요한 것부터 MAX_ITEMS 개만 넣고 나머지 수를 적는다."""
    shown = items[:MAX_ITEMS]
    names = list(dict.fromkeys(d["corp_name"] for d in items))
    who = names[0] + (f" 외 {len(names) - 1}곳" if len(names) > 1 else "")
    subject = f"[DART 알림] {who} 공시 {len(items)}건"
    body = "\n\n".join(format_item(d, headlines.get(d["rcept_no"], [])) for d in shown)
    if len(items) > len(shown):
        body += f"\n\n… 외 {len(items) - len(shown)}건은 웹 화면의 공시 피드에서 확인하세요."
    body += "\n\n공시 정보 알림이며 투자 권유가 아닙니다."
    return subject, body


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
    """senders: {"email": EmailSender, "telegram": TelegramSender}. 없는 채널은 건너뛴다."""
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
