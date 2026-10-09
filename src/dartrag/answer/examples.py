"""예시 질문의 답을 답변 캐시에 미리 넣기 (dartrag cache warm, 작업자의 warm_examples).

CPU 만 있는 서버에서는 처음 묻는 질문의 답이 1분 넘게 걸린다. 채팅 첫 화면의 예시 질문
(EXAMPLE_QUESTIONS)을 미리 답해 캐시에 넣어 두면, 방문자가 예시를 누를 때 바로 답한다.

캐시 키에는 질문, 검색 범위, 모델, 프롬프트 버전, 데이터 버전이 들어간다. 그래서 화면에서 예시를
눌렀을 때(web/chat.py 의 새 대화, 검색 범위 지정 없음)와 똑같이 질문을 해석해 같은 키로 넣는다.
새 공시를 색인해 데이터 버전이 오르면 예전 답은 쓰이지 않으므로, 작업자가 주기마다 지금 버전의
답이 있는지 보고 없는 질문만 다시 만든다 (기업 대시보드 미리 만들기와 같은 방식).
"""

import logging
import time
from collections.abc import Callable, Iterable

from dartrag.answer.conversation import resolve
from dartrag.search import SearchFilter

log = logging.getLogger(__name__)


def example_request(question: str, companies: list[tuple[str, str]]) -> tuple[str, SearchFilter]:
    """화면에서 예시를 눌렀을 때 답변기에 넘어가는 (질문, 검색 범위).

    새 대화에서 회사·연도를 따로 고르지 않고 물었을 때와 같다: 질문 속 회사로 범위를 좁힌다."""
    resolved = resolve(question, None, companies)
    return resolved.question, SearchFilter(corp_codes=resolved.context.corp_codes)


def warm(
    answerer,
    repo,
    questions: Iterable[str],
    *,
    echo: Callable[[str], None] | None = None,
) -> dict:
    """캐시에 없는 예시 질문만 답해서 넣는다. 한 질문이 실패해도 나머지는 계속한다.

    돌려주는 값: {"questions", "built"(새로 넣음), "fresh"(이미 있음), "errors"} 또는
    {"skipped": 이유}."""
    say = echo or (lambda line: None)
    cache = answerer.cache
    if cache is None:
        return {"skipped": "답변 캐시가 꺼져 있음 (ANSWER_CACHE, REDIS_URL)"}
    if not repo.has_indexed_filings():
        return {"skipped": "아직 색인한 공시가 없음 (dartrag run 이 끝난 뒤 다시 하세요)"}
    questions = [q.strip() for q in questions if q.strip()]
    companies = repo.listed_companies()
    built, fresh, errors = 0, 0, []
    for q in questions:
        question, flt = example_request(q, companies)
        if cache.has(question, flt):
            fresh += 1
            say(f"이미 있음  {q}")
            continue
        started = time.monotonic()
        try:
            answerer.answer(question, flt)
        except Exception as e:  # noqa: BLE001 - 한 질문 때문에 나머지를 멈추지 않는다
            conn = getattr(repo, "conn", None)
            if conn is not None:
                conn.rollback()
            errors.append(f"{q}: {type(e).__name__}: {e}")
            log.warning("예시 질문 답변 실패: %s", type(e).__name__)
            say(f"실패      {q} ({type(e).__name__}: {e})")
            continue
        seconds = time.monotonic() - started
        if cache.has(question, flt):
            built += 1
            say(f"새로 넣음  {q} ({seconds:.1f}초)")
        else:
            # 거절한 질문이거나, 대체 모델이 답했거나, 캐시 저장이 실패한 경우
            errors.append(f"{q}: 답이 캐시에 남지 않음 (거절한 질문이거나 대체 모델이 답함)")
            say(f"못 넣음    {q} (거절한 질문이거나 대체 모델이 답함)")
    return {"questions": len(questions), "built": built, "fresh": fresh, "errors": errors}
