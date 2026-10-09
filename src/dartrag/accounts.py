"""계정 지우기. 화면의 탈퇴, 체험 계정 끝내기(로그아웃), 기한이 지난 체험 계정 정리(작업자)가
같은 경로를 쓴다: 그 사용자의 기록과 계정을 한 트랜잭션에서 지우고(Repository.delete_user),
LLM 추적(Langfuse)에 남은 그 사용자 가명의 기록도 지우도록 요청한다.
"""

import logging

log = logging.getLogger(__name__)


def forget_traces(tracer, user_id: int) -> None:
    """그 사용자의 LLM 추적 삭제 요청. 실패해도 계정 삭제는 그대로 끝난다."""
    if tracer is None:
        return
    try:
        tracer.forget_user(user_id)
    except Exception as e:  # noqa: BLE001 - 추적 삭제 실패로 계정 삭제가 깨지면 안 된다
        log.warning("계정 삭제 후 추적 삭제 실패: %s", type(e).__name__)


def purge_expired_guests(repo, tracer, limit: int = 500) -> int:
    """기한(GUEST_HOURS)이 지난 체험 계정을 탈퇴와 같은 경로로 지운다. 지운 계정 수.

    한 번에 limit 개까지만 지우고 나머지는 다음 회차에 지운다."""
    removed = 0
    for user_id in repo.expired_guests(limit):
        if repo.delete_user(user_id):
            removed += 1
            forget_traces(tracer, user_id)
    return removed
