"""사용자 👎 평가를 평가셋 후보 문항으로 바꾼다.

정답은 사람이 원문을 보고 채워야 하므로 note 에 사유와 당시 답변을 남기고,
검수 전까지는 별도 파일(eval/feedback_candidates.jsonl)에 둔다.
"""

from dartrag.eval.cases import EvalCase

REASON_LABEL = {
    "wrong_number": "숫자가 틀림",
    "wrong_source": "출처가 맞지 않음",
    "not_found": "답이 있는데 못 찾음",
    "unhelpful": "도움이 안 됨",
    "other": "기타",
}


def feedback_to_cases(rows: list[dict]) -> list[EvalCase]:
    cases = []
    for r in rows:
        qp = r.get("question_payload") or {}
        question = qp.get("resolved_question") or r["question"]
        context = qp.get("context") or {}
        reason = REASON_LABEL.get(r.get("reason") or "", "사유 없음")
        note = f"사용자 평가 👎 ({reason})"
        if r.get("comment"):
            note += f": {r['comment']}"
        note += f" | 당시 답변: {r['answer'][:300]}"
        category = "text"
        if r.get("reason") == "wrong_number":
            category = "numeric"
        cases.append(
            EvalCase(
                id=f"feedback-{r['message_id']}",
                question=question,
                category=category,
                corp_codes=list(context.get("corp_codes", [])),
                source="feedback",
                note=note,
            )
        )
    return cases
