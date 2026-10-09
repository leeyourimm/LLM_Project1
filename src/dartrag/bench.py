"""답변 속도 재기 (dartrag bench).

실행 중인 API 의 스트리밍 답변(POST /api/ask/stream)에 질문 N개를 동시에 C개씩 보내고
질문마다 두 시간을 잰다.

- 첫 글자: 요청을 보낸 때부터 첫 token 이벤트까지. 검색 시간도 들어간다.
  token 없이 끝난 답(근거를 못 찾음)은 done 까지를 첫 글자로 본다 (그때 화면에 답이 뜬다).
- 전체: done 이벤트까지.

p50·p95 를 docs/design.md 10절의 기준(첫 토큰 2초 이내, 전체 응답 p95 8초 이내)과 비교한다.
첫 글자는 p95 가 기준 안이면 통과로 본다.
"""

import json
import math
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

# docs/design.md 10절 "Q&A 첫 토큰 2초 이내, 전체 응답 p95 8초 이내"
SLO_FIRST_TOKEN_S = 2.0
SLO_TOTAL_P95_S = 8.0
# p95 가 사실상 가장 느린 값과 같아지지 않으려면 이 정도는 재야 한다
MIN_SAMPLES_FOR_P95 = 20
STREAM_PATH = "/api/ask/stream"


@dataclass
class Question:
    text: str
    stocks: list[str] = field(default_factory=list)
    case_id: str | None = None


@dataclass
class Result:
    question: Question
    ok: bool
    first_token_s: float | None = None
    total_s: float | None = None
    status: int | None = None  # HTTP 상태
    error: str | None = None
    tokens: int = 0  # 받은 token 이벤트 수
    cached: bool = False
    refused: bool = False
    found: bool | None = None
    model: str | None = None


def percentile(values: Iterable[float], p: float) -> float | None:
    """선형 보간 백분위 (numpy 기본 방식과 같다). 값이 없으면 None."""
    xs = sorted(values)
    if not xs:
        return None
    rank = (len(xs) - 1) * p / 100
    lo, hi = math.floor(rank), math.ceil(rank)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def load_questions(paths: list[Path], categories: list[str] | None = None) -> list[Question]:
    """평가 문항 파일(JSONL)에서 질문과 종목코드를 읽는다.

    categories 를 주지 않으면 adversarial 을 뺀다. 투자 추천·인젝션 질문은 모델에 보내지 않고
    바로 거절하므로 속도를 재는 데 맞지 않다."""
    from dartrag.eval.cases import load_cases

    wanted = set(categories) if categories else None
    return [
        Question(c.question, list(c.stocks), c.id)
        for c in load_cases(*paths)
        if (c.category in wanted if wanted else c.category != "adversarial")
    ]


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    """Server-Sent Events 줄들 → (이벤트 이름, data JSON)."""
    event, data = "message", []
    for line in lines:
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield event, json.loads("\n".join(data))


def _http_error(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if resp.status_code == 429:
        return "요청 한도(429)"
    if resp.status_code == 401:
        return "로그인 필요(401)"
    return f"HTTP {resp.status_code}" + (f": {detail}" if isinstance(detail, str) else "")


def ask_once(
    client: httpx.Client, q: Question, clock: Callable[[], float] = time.perf_counter
) -> Result:
    start = clock()
    first: float | None = None
    tokens = 0
    body = {"question": q.text, "stocks": q.stocks}
    try:
        with client.stream("POST", STREAM_PATH, json=body) as resp:
            if resp.status_code != 200:
                resp.read()
                elapsed = clock() - start
                return Result(q, False, None, elapsed, resp.status_code, _http_error(resp))
            for event, data in parse_sse(resp.iter_lines()):
                now = clock() - start
                if event == "token":
                    tokens += 1
                    first = now if first is None else first
                elif event == "error":
                    detail = data.get("detail") or "오류"
                    return Result(q, False, first, now, 200, detail, tokens)
                elif event == "done":
                    return Result(
                        q,
                        True,
                        first if first is not None else now,
                        now,
                        200,
                        tokens=tokens,
                        cached=bool(data.get("cached")),
                        refused=bool(data.get("refused")),
                        found=data.get("found"),
                        model=data.get("model"),
                    )
        return Result(q, False, first, clock() - start, 200, "done 이벤트 없이 끊김", tokens)
    except httpx.HTTPError as e:
        return Result(q, False, first, clock() - start, error=f"연결 오류 ({type(e).__name__})")


def run_bench(
    url: str,
    questions: list[Question],
    *,
    requests: int,
    concurrency: int = 1,
    timeout: float = 120,
    on_result: Callable[[int, Result], None] | None = None,
) -> tuple[list[Result], float]:
    """(결과들, 전체 걸린 초). 질문이 requests 보다 적으면 처음부터 다시 쓴다."""
    if not questions:
        raise ValueError("보낼 질문이 없습니다")
    jobs = [questions[i % len(questions)] for i in range(requests)]
    results: list[Result] = []
    started = time.perf_counter()
    limits = httpx.Limits(max_connections=max(concurrency, 1) + 2)
    with (
        httpx.Client(
            base_url=url.rstrip("/"), timeout=httpx.Timeout(timeout, connect=10), limits=limits
        ) as client,
        ThreadPoolExecutor(max(concurrency, 1)) as pool,
    ):
        futures = [pool.submit(ask_once, client, q) for q in jobs]
        for done in as_completed(futures):
            results.append(done.result())
            if on_result:
                on_result(len(results), results[-1])
    return results, time.perf_counter() - started


def summarize(
    results: list[Result],
    elapsed: float,
    *,
    slo_first_token: float = SLO_FIRST_TOKEN_S,
    slo_total_p95: float = SLO_TOTAL_P95_S,
    concurrency: int = 1,
) -> dict:
    ok = [r for r in results if r.ok]
    first = [r.first_token_s for r in ok if r.first_token_s is not None]
    total = [r.total_s for r in ok if r.total_s is not None]

    def stats(xs: list[float]) -> dict:
        return {"p50": percentile(xs, 50), "p95": percentile(xs, 95), "max": max(xs, default=None)}

    first_stats, total_stats = stats(first), stats(total)
    return {
        "requests": len(results),
        "concurrency": concurrency,
        "ok": len(ok),
        "errors": dict(Counter(r.error for r in results if not r.ok)),
        "cached": sum(r.cached for r in ok),
        "refused": sum(r.refused for r in ok),
        "not_found": sum(r.found is False and not r.refused for r in ok),
        "models": dict(Counter(r.model or "?" for r in ok)),
        "elapsed_s": elapsed,
        "per_second": len(ok) / elapsed if elapsed > 0 else None,
        "first_token_s": first_stats,
        "total_s": total_stats,
        "first_token_within_slo": (
            sum(x <= slo_first_token for x in first) / len(first) if first else None
        ),
        "slo": {"first_token_s": slo_first_token, "total_p95_s": slo_total_p95},
        "passed": {
            "first_token": first_stats["p95"] is not None and first_stats["p95"] <= slo_first_token,
            "total": total_stats["p95"] is not None and total_stats["p95"] <= slo_total_p95,
        },
    }


def _s(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}초"


def _cell(text: str, width: int, *, right: bool = True) -> str:
    """터미널에서 한글은 두 칸을 차지하므로 보이는 폭으로 맞춘다."""
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    pad = " " * max(0, width - shown)
    return pad + text if right else text + pad


def format_report(summary: dict) -> str:
    s = summary
    slo, passed = s["slo"], s["passed"]
    errors = sum(s["errors"].values())
    lines = [
        f"질문 {s['requests']}개 (동시 {s['concurrency']}개): 성공 {s['ok']}, 실패 {errors}, "
        f"캐시 {s['cached']}, 거절 {s['refused']}, 못 찾음 {s['not_found']}",
        f"모두 {s['elapsed_s']:.1f}초"
        + (f", 1초에 {s['per_second']:.2f}개 답함" if s["per_second"] else ""),
        "",
        _cell("", 8, right=False) + "".join(_cell(h, 10) for h in ("p50", "p95", "최대")),
    ]
    for label, key in (("첫 글자", "first_token_s"), ("전체", "total_s")):
        st = s[key]
        cells = [_cell(_s(st[k]), 10) for k in ("p50", "p95", "max")]
        lines.append(_cell(label, 8, right=False) + "".join(cells))
    lines += ["", "SLO (docs/design.md 10절)"]
    first_p95, total_p95 = s["first_token_s"]["p95"], s["total_s"]["p95"]
    within = s["first_token_within_slo"]
    share = ""
    if within is not None:
        share = f" ({slo['first_token_s']:g}초 안에 시작한 질문 {within:.0%})"
    lines.append(
        f"{'✅' if passed['first_token'] else '❌'} 첫 글자 p95 {_s(first_p95)},"
        f" 기준 {slo['first_token_s']:g}초 이내{share}"
    )
    lines.append(
        f"{'✅' if passed['total'] else '❌'} 전체 p95 {_s(total_p95)},"
        f" 기준 {slo['total_p95_s']:g}초 이내"
    )
    if s["models"]:
        lines.append("답한 모델: " + ", ".join(f"{m} {n}개" for m, n in s["models"].items()))
    for error, n in s["errors"].items():
        lines.append(f"실패: {error} {n}개")
    lines += hints(s)
    return "\n".join(lines)


def hints(s: dict) -> list[str]:
    out = []
    if any("429" in e for e in s["errors"]):
        out.append(
            "→ 요청 한도에 걸렸습니다. 잴 때는 서버를 "
            "RATE_ASK_PER_MINUTE=0 RATE_ASK_PER_DAY=0 dartrag serve 로 켜세요"
        )
    if any("401" in e for e in s["errors"]):
        out.append("→ 로그인을 켠 서버입니다. AUTH_REQUIRED=false 로 켠 내 컴퓨터 서버에서 재세요")
    if any("연결 오류" in e for e in s["errors"]):
        out.append(
            "→ 서버에 연결하지 못했습니다. 다른 터미널에서 dartrag serve 가 켜져 있는지 확인하세요"
        )
    if s["cached"]:
        out.append(
            f"→ 캐시에서 바로 나온 답 {s['cached']}개는 모델을 거치지 않아 빠릅니다. "
            "빼고 재려면 서버를 ANSWER_CACHE=false 로 켜세요"
        )
    if 0 < s["ok"] < MIN_SAMPLES_FOR_P95:
        out.append(
            f"→ 성공한 질문이 {s['ok']}개라 p95 는 가장 느린 값에 가깝습니다. "
            f"-n {MIN_SAMPLES_FOR_P95} 이상으로 재면 더 믿을 만합니다"
        )
    return out


def to_json(results: list[Result], summary: dict) -> str:
    return json.dumps(
        {"summary": summary, "results": [asdict(r) for r in results]},
        ensure_ascii=False,
        indent=2,
    )
