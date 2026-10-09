"""근거 기반 답변 프롬프트."""

from dartrag.answer.llm import Message
from dartrag.search import SearchHit

NOT_FOUND = "제공된 공시에서 답을 찾지 못했습니다."

SYSTEM = f"""당신은 한국 상장사의 DART 공시를 읽고 질문에 답하는 분석 도우미입니다.

규칙:
1. 아래 [출처] 로 주어진 공시 발췌만 근거로 답합니다. 배경지식이나 추측으로 내용을 보태지 않습니다.
2. 사실을 말하는 문장마다 끝에 근거 출처 번호를 [1], [2] 처럼 붙입니다.
   출처에 없는 번호는 쓰지 않습니다.
3. 숫자는 출처에 적힌 값과 단위를 그대로 옮깁니다.
   단위 환산이나 계산을 했다면 계산식을 함께 적습니다.
4. 출처에 답이 없으면 다른 말 없이 "{NOT_FOUND}" 라고만 답합니다.
5. 매수·매도 추천이나 주가 전망은 하지 않습니다.
6. 한국어로, 질문에 바로 답하는 문장부터 간결하게 씁니다."""


def format_source(i: int, hit: SearchHit) -> str:
    c = hit.chunk
    header = f"[{i}] {c['corp_name']} | {c['report_nm']} | {' > '.join(c['section_path'])}"
    if c.get("unit"):
        header += f" | 단위: {c['unit']}"
    return f"{header}\n{c['body']}"


def build_messages(question: str, hits: list[SearchHit]) -> list[Message]:
    sources = "\n\n".join(format_source(i, h) for i, h in enumerate(hits, start=1))
    return [
        Message("system", SYSTEM),
        Message("user", f"[출처]\n{sources}\n\n[질문]\n{question}"),
    ]
