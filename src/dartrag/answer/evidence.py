"""출처 패널에 보일 근거 위치: 답변이 원문의 어느 부분에 기댔는지.

- passage(hit): 화면에 보일 원문과, 그 안에서 검색된 청크(답변이 인용한 문단)의 위치.
  모델은 청크 앞뒤 문단까지 붙인 넓은 맥락(context_body)을 읽으므로 화면도 그것을 보여 주고
  인용한 문단을 표시한다.
- quoted(answer): 출처 번호별로, 그 번호를 붙인 답변 문장의 숫자가 원문 어디에 있는지.
  숫자 검증기(numbers.py)와 같은 규칙으로 찾으므로 "확인된 숫자"만 표시된다.
위치는 모두 파이썬 문자열 기준(유니코드 코드 포인트) [시작, 끝) 이다.
"""

from dartrag.answer.numbers import quoted_spans, split_sentences


def shown_text(hit) -> tuple[str, list[int] | None]:
    """(화면에 보일 원문, 그 안에서 인용 문단의 [시작, 끝]). 넓힌 맥락이 없으면 위치는 None."""
    body = hit.chunk.get("body", "")
    context = hit.chunk.get("context_body")
    if context and context != body and (start := context.find(body)) >= 0:
        return context, [start, start + len(body)]
    return body, None


def passage(hit) -> dict:
    """source_dict 에 붙이는 필드: context(앞뒤 문단을 포함한 원문), highlight(인용 문단 위치)."""
    text, highlight = shown_text(hit)
    if highlight is None:
        return {"context": None, "highlight": None}
    return {"context": text, "highlight": highlight}


def quoted(answer) -> dict[int, list[list[int]]]:
    """인용 번호 → 그 번호를 붙인 문장의 숫자가 원문(passage 의 context 또는 body)에서 있는 위치."""
    sentences = split_sentences(answer.text)
    out: dict[int, list[list[int]]] = {}
    for c in answer.citations:
        mine = [s for s in sentences if f"[{c.number}]" in s]
        text, _ = shown_text(c.hit)
        spans = quoted_spans(mine, text, c.hit.chunk.get("unit"))
        if spans:
            out[c.number] = [list(s) for s in spans]
    return out
