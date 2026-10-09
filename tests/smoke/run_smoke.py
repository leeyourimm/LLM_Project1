"""실제 서비스와 실제 모델로 질문을 끝까지 돌려 보는 종단 점검 (smoke test).

단위 테스트는 가짜 검색기·LLM 으로 돌아서, 진짜 백엔드를 조립하는 코드(dartrag.factory)의
배선 문제는 잡지 못한다. 예: Backends._get 이 보통 Lock 이어서, vector 를 만들다 qdrant 를
꺼내는 순간 첫 질문이 영원히 멈추던 문제. 여기서는 Postgres, Redis, Qdrant, OpenSearch(nori),
Ollama 를 실제로 띄워 두고 다음을 확인한다.

  1. 빈 DB 에 질문하면 "아직 수집·색인한 공시가 없어" 안내가 곧바로 온다
  2. 원문(tests/fixtures/sample_report.xml)을 원문 저장소에 넣고, dartrag parse·index 를
     실제 프로세스로 실행한다 (OpenDART 는 부르지 않는다)
  3. dartrag serve 의 /api/ask/stream 이 meta → sources → token… → done(출처 1개 이상) 순서로 답한다
  4. 같은 질문을 다시 하면 Redis 답변 캐시에서 나온다

모든 네트워크 호출과 하위 프로세스에 시간 제한을 두어, 어디선가 멈추면 기다리지 않고 실패한다.

GitHub Actions 의 .github/workflows/smoke.yml 이 실행한다. DATABASE_URL 등 설정이 가리키는 DB 에
시험용 공시를 넣으므로, 비어 있는 시험용 DB 에서 SMOKE=1 을 붙여야만 돈다:

    SMOKE=1 python tests/smoke/run_smoke.py

pytest 가 모으지 않도록 파일 이름을 test_ 로 시작하지 않는다.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "sample_report.xml"

CORP_CODE, CORP_NAME, STOCK_CODE = "00126380", "삼성전자", "005930"
RCEPT_NO = "20250311000001"
REPORT_NM = "사업보고서 (2024.12)"
QUESTION = "삼성전자의 사업은 어떤 부문으로 구성되어 있나요?"
NO_DATA_TEXT = "아직 수집·색인한 공시가 없어"

PORT = int(os.environ.get("SMOKE_PORT", "8787"))
BASE = f"http://127.0.0.1:{PORT}"
LOG_DIR = Path(os.environ.get("SMOKE_LOG_DIR") or ROOT / "reports" / "smoke")

# 시간 제한(초)
SERVICE_WAIT = 300  # 서비스가 뜨기까지 (OpenSearch 가 가장 느리다)
CLI_TIMEOUT = {"migrate": 120, "parse": 120, "index": 600}  # index 는 임베딩 모델을 불러온다
HEALTH_WAIT = 120
EMPTY_ANSWER = 30  # 빈 DB 안내는 모델을 불러오지 않으므로 금방 와야 한다
FIRST_ANSWER = 300  # 첫 질문: 임베딩·리랭커 모델을 불러오고 LLM 이 답한다
CACHED_ANSWER = 60
HTTP_TIMEOUT = httpx.Timeout(30, connect=10)  # 읽기 30초: 서버는 10초마다 keep-alive 를 보낸다


class SmokeFailure(AssertionError):
    pass


def check(ok: bool, message: str) -> None:
    if not ok:
        raise SmokeFailure(message)


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.splitlines()[-lines:])


# --- 서비스 ------------------------------------------------------------------


def wait_until(name: str, probe, timeout: float = SERVICE_WAIT) -> None:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            if probe():
                print(f"{name}: 준비됨", flush=True)
                return
            last = "아직 준비 중"
        except Exception as e:  # noqa: BLE001 - 뜨는 중에는 연결 거부 등이 정상이다
            last = repr(e)
        time.sleep(2)
    raise SmokeFailure(f"{name} 이(가) {timeout:.0f}초 안에 준비되지 않았습니다: {last}")


def wait_for_services(settings) -> None:
    import psycopg
    import redis

    def postgres() -> bool:
        psycopg.connect(settings.database_url, connect_timeout=5).close()
        return True

    def redis_ok() -> bool:
        client = redis.Redis.from_url(
            settings.redis_url, socket_timeout=5, socket_connect_timeout=5
        )
        try:
            return bool(client.ping())
        finally:
            client.close()

    def qdrant() -> bool:
        return httpx.get(f"{settings.qdrant_url}/readyz", timeout=5).status_code == 200

    def opensearch() -> bool:
        r = httpx.get(
            f"{settings.opensearch_url}/_cluster/health",
            params={"wait_for_status": "yellow", "timeout": "5s"},
            timeout=10,
        )
        return r.status_code == 200 and r.json().get("status") in ("green", "yellow")

    def ollama() -> bool:
        return httpx.get(f"{settings.ollama_url}/api/version", timeout=5).status_code == 200

    wait_until("Postgres", postgres)
    wait_until("Redis", redis_ok)
    wait_until("Qdrant", qdrant)
    wait_until("OpenSearch", opensearch)
    wait_until("Ollama", ollama)

    plugins = httpx.get(
        f"{settings.opensearch_url}/_cat/plugins", params={"format": "json"}, timeout=10
    ).json()
    check(
        any(p.get("component") == "analysis-nori" for p in plugins),
        f"OpenSearch 에 nori 플러그인이 없습니다: {plugins}",
    )
    models = httpx.get(f"{settings.ollama_url}/api/tags", timeout=10).json().get("models", [])
    names = {m["name"] for m in models}
    wanted = settings.llm_model if ":" in settings.llm_model else f"{settings.llm_model}:latest"
    check(wanted in names, f"Ollama 에 {wanted} 모델이 없습니다 (있는 모델: {sorted(names)})")


# --- dartrag 명령과 서버 -------------------------------------------------------


def dartrag_cmd() -> list[str]:
    exe = shutil.which("dartrag")
    return [exe] if exe else [sys.executable, "-m", "dartrag.cli"]


def run_cli(*args: str) -> str:
    cmd = [*dartrag_cmd(), *args]
    timeout = CLI_TIMEOUT[args[0]]
    print(f"$ dartrag {' '.join(args)}  (시간 제한 {timeout}초)", flush=True)
    started = time.monotonic()
    try:
        # 시간 제한을 넘기면 subprocess.run 이 그 프로세스(PID)를 죽인다
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        raise SmokeFailure(
            f"dartrag {' '.join(args)} 이(가) {timeout}초 안에 끝나지 않았습니다 (멈춤?)\n"
            f"{tail(out + err)}"
        ) from None
    elapsed = time.monotonic() - started
    output = proc.stdout + proc.stderr
    print(tail(output, 30), flush=True)
    check(
        proc.returncode == 0,
        f"dartrag {' '.join(args)} 실패 (종료 코드 {proc.returncode})\n{tail(output)}",
    )
    print(f"→ {elapsed:.1f}초", flush=True)
    return proc.stdout


class Server:
    """dartrag serve 를 실제 프로세스로 띄운다. 끝낼 때는 그 PID 에만 신호를 보낸다."""

    def __init__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path = LOG_DIR / "server.log"
        self._log = open(self.log_path, "w")  # stop() 에서 닫는다
        self.proc = subprocess.Popen(
            [*dartrag_cmd(), "serve", "--port", str(PORT)],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        print(f"dartrag serve 시작 (PID {self.proc.pid}, 로그 {self.log_path})", flush=True)

    def log_tail(self, lines: int = 80) -> str:
        return tail(self.log_path.read_text(errors="replace"), lines)

    def wait_healthy(self) -> None:
        deadline = time.monotonic() + HEALTH_WAIT
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise SmokeFailure(
                    f"서버가 종료 코드 {self.proc.returncode} 로 꺼졌습니다\n{self.log_tail()}"
                )
            try:
                r = httpx.get(f"{BASE}/api/health", timeout=5)
                if r.status_code == 200 and r.json().get("ok"):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise SmokeFailure(f"/api/health 가 {HEALTH_WAIT}초 안에 응답하지 않았습니다")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self._log.close()


# --- 질문 (Server-Sent Events) -------------------------------------------------


def ask_stream(question: str, deadline: float) -> tuple[list[tuple[str, dict]], float]:
    """/api/ask/stream 의 이벤트 [(이름, 데이터)] 와 걸린 시간.

    서버는 이벤트 사이에 10초마다 keep-alive 주석을 보내므로, 안에서 멈춰도 연결은 살아 있다.
    그래서 읽기 시간 제한과 별도로 전체 시간 제한(deadline)을 직접 잰다."""
    events: list[tuple[str, dict]] = []
    keepalives = 0
    started = time.monotonic()
    name: str | None = None
    data: list[str] = []

    def flush() -> None:
        nonlocal name, data
        if name:
            events.append((name, json.loads("\n".join(data)) if data else {}))
        name, data = None, []

    try:
        with (
            httpx.Client(base_url=BASE, timeout=HTTP_TIMEOUT) as client,
            client.stream("POST", "/api/ask/stream", json={"question": question}) as resp,
        ):
            if resp.status_code != 200:
                resp.read()
                raise SmokeFailure(f"/api/ask/stream {resp.status_code}: {resp.text[:500]}")
            for line in resp.iter_lines():
                if time.monotonic() - started > deadline:
                    raise SmokeFailure(
                        f"답변이 {deadline:.0f}초 안에 끝나지 않았습니다 (멈춤?). "
                        f"받은 이벤트 {[n for n, _ in events]}, keep-alive {keepalives}번"
                    )
                if line.startswith(":"):
                    keepalives += 1
                elif line.startswith("event:"):
                    name = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data.append(line.removeprefix("data:").strip())
                elif not line:
                    flush()
            flush()
    except httpx.HTTPError as e:
        raise SmokeFailure(
            f"/api/ask/stream 요청 실패: {e!r}. 받은 이벤트 {[n for n, _ in events]}"
        ) from None
    return events, time.monotonic() - started


def describe(events: list[tuple[str, dict]]) -> str:
    counts: dict[str, int] = {}
    for n, _ in events:
        counts[n] = counts.get(n, 0) + 1
    return ", ".join(f"{n}×{c}" for n, c in counts.items())


def check_no_error(events: list[tuple[str, dict]]) -> None:
    errors = [d.get("detail") for n, d in events if n == "error"]
    check(not errors, f"error 이벤트: {errors}")


def check_answer_stream(events: list[tuple[str, dict]], *, cached: bool) -> dict:
    """meta → sources → token… → done, done 에 출처가 하나 이상."""
    check_no_error(events)
    names = [n for n, _ in events]
    check(bool(names) and names[0] == "meta", f"첫 이벤트가 meta 가 아닙니다: {names[:5]}")
    check(
        names[-1] == "done" and names.count("done") == 1,
        f"done 이 맨 끝에 한 번만 와야 합니다: {describe(events)}",
    )
    check("sources" in names, f"sources 이벤트가 없습니다: {describe(events)}")
    check("token" in names, f"token 이벤트가 없습니다: {describe(events)}")
    check(
        names.index("sources") < names.index("token"),
        f"sources 가 token 보다 먼저 와야 합니다: {names[:5]}",
    )
    sources = events[names.index("sources")][1].get("sources") or []
    check(len(sources) >= 1, "sources 이벤트에 출처가 없습니다")
    meta, done = events[0][1], events[-1][1]
    check(
        done.get("conversation_id") == meta.get("conversation_id"),
        f"meta 와 done 의 대화 번호가 다릅니다: {meta} / {done.get('conversation_id')}",
    )
    check(len(done.get("sources") or []) >= 1, "done 에 출처가 하나도 없습니다")
    check(
        any(s.get("corp_name") == CORP_NAME for s in done["sources"]),
        f"출처에 {CORP_NAME} 공시가 없습니다: {[s.get('corp_name') for s in done['sources']]}",
    )
    text = "".join(d.get("text", "") for n, d in events if n == "token")
    check(bool(text.strip()) and bool(done.get("answer", "").strip()), "답변 글이 비어 있습니다")
    check(
        done.get("cached") is cached,
        f"cached 가 {cached} 이어야 합니다: {done.get('cached')}",
    )
    return done


# --- 시험용 공시 넣기 ---------------------------------------------------------


def seed(settings) -> None:
    """OpenDART 대신 수집 단계가 하는 일을 직접 한다: 회사·공시를 DB 에, 원문 zip 을 저장소에."""
    from dartrag.dart.models import Corp, Filing
    from dartrag.dart.reports import parse_report_name
    from dartrag.db import Repository
    from dartrag.storage import make_raw_store
    from dartrag.storage.raw import document_key

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{RCEPT_NO}.xml", FIXTURE.read_bytes())
    key = document_key(RCEPT_NO)
    make_raw_store(settings).put(key, buf.getvalue())

    filing = Filing(
        corp_code=CORP_CODE,
        corp_name=CORP_NAME,
        stock_code=STOCK_CODE,
        report_nm=REPORT_NM,
        rcept_no=RCEPT_NO,
        rcept_dt=date(2025, 3, 11),
    )
    report = parse_report_name(REPORT_NM)
    check(report is not None, f"정기보고서 이름을 해석하지 못했습니다: {REPORT_NM}")
    repo = Repository.connect(settings.database_url)
    try:
        repo.upsert_companies(
            [Corp(corp_code=CORP_CODE, corp_name=CORP_NAME, stock_code=STOCK_CODE)]
        )
        repo.upsert_filing(filing, report, report.reprt_code(12), key)
    finally:
        repo.conn.close()
    print(f"{CORP_NAME} {REPORT_NM} ({RCEPT_NO}) 원문을 {key} 에 넣었습니다", flush=True)


def has_indexed_filings(settings) -> bool:
    from dartrag.db import Repository

    repo = Repository.connect(settings.database_url)
    try:
        return repo.has_indexed_filings()
    finally:
        repo.conn.close()


# --- 순서 --------------------------------------------------------------------


def main() -> int:
    if os.environ.get("SMOKE") != "1":
        print("SMOKE=1 일 때만 실행합니다 (설정이 가리키는 DB 에 시험용 공시를 넣습니다).")
        return 0

    from dartrag.config import get_settings

    settings = get_settings()
    timings: dict[str, float] = {}

    step("서비스 확인")
    print(
        f"임베딩 {settings.embed_model}, 리랭커 {settings.rerank_model or '(끔)'}, "
        f"LLM {settings.llm_model}",
        flush=True,
    )
    wait_for_services(settings)

    step("DB 스키마")
    run_cli("migrate")
    check(
        not has_indexed_filings(settings),
        "색인한 공시가 이미 있습니다. 비어 있는 시험용 DB 에서 실행하세요.",
    )

    step("서버 시작")
    server = Server()
    try:
        t0 = time.monotonic()
        server.wait_healthy()
        timings["서버 시작"] = time.monotonic() - t0

        step("빈 DB 에 질문")
        events, elapsed = ask_stream(QUESTION, EMPTY_ANSWER)
        timings["빈 DB 답변"] = elapsed
        print(f"{describe(events)} ({elapsed:.1f}초)", flush=True)
        check_no_error(events)
        names = [n for n, _ in events]
        check(names[:1] == ["meta"] and names[-1:] == ["done"], f"이벤트 순서: {names}")
        done = events[-1][1]
        check(NO_DATA_TEXT in done.get("answer", ""), f"빈 DB 안내가 아닙니다: {done}")
        check(done.get("found") is False and not done.get("sources"), f"빈 DB 답변: {done}")

        step("시험용 공시 넣기 → parse → index")
        seed(settings)
        out = run_cli("parse")
        check("공시 1건" in out, f"파싱한 공시가 1건이 아닙니다: {out.strip()}")
        t0 = time.monotonic()
        out = run_cli("index")
        timings["dartrag index"] = time.monotonic() - t0
        check("공시 1건" in out, f"색인한 공시가 1건이 아닙니다: {out.strip()}")
        check(has_indexed_filings(settings), "index 뒤에도 색인한 공시가 없습니다")

        step("질문 (실제 검색·리랭커·LLM)")
        events, elapsed = ask_stream(QUESTION, FIRST_ANSWER)
        timings["첫 답변"] = elapsed
        print(f"{describe(events)} ({elapsed:.1f}초)", flush=True)
        done = check_answer_stream(events, cached=False)
        print(f"답변: {done['answer'][:300]}", flush=True)
        print(
            f"출처 {len(done['sources'])}개, 모델 {done.get('model')}, "
            f"found={done.get('found')}, 경고 {done.get('warnings')}",
            flush=True,
        )

        step("같은 질문 다시 (Redis 답변 캐시)")
        events, elapsed = ask_stream(QUESTION, CACHED_ANSWER)
        timings["캐시 답변"] = elapsed
        print(f"{describe(events)} ({elapsed:.1f}초)", flush=True)
        check_answer_stream(events, cached=True)
    except SmokeFailure:
        print(f"\n--- 서버 로그 (끝부분) ---\n{server.log_tail()}", flush=True)
        raise
    finally:
        server.stop()

    step("통과")
    for k, v in timings.items():
        print(f"{k}: {v:.1f}초")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### 실제 서비스 종단 점검 통과\n\n| 단계 | 시간 |\n|---|---|\n")
            f.writelines(f"| {k} | {v:.1f}초 |\n" for k, v in timings.items())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as e:
        print(f"\n실패: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
