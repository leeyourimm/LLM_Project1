import logging
from pathlib import Path
from typing import Annotated

import typer

from dartrag.config import get_settings
from dartrag.dart import DartApiError, OpenDartClient
from dartrag.db import Repository
from dartrag.pipeline.collect import collect
from dartrag.pipeline.parse import parse_filings
from dartrag.search.types import SearchFilter
from dartrag.storage import make_raw_store

app = typer.Typer(help="DART 공시 분석 서비스 도구")

# 0단계 기본 대상: KOSPI 대형주 15개사
DEFAULT_STOCKS = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "373220",  # LG에너지솔루션
    "207940",  # 삼성바이오로직스
    "005380",  # 현대차
    "000270",  # 기아
    "068270",  # 셀트리온
    "005490",  # POSCO홀딩스
    "035420",  # NAVER
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "105560",  # KB금융
    "055550",  # 신한지주
    "035720",  # 카카오
    "012330",  # 현대모비스
]


@app.command()
def migrate():
    """DB 스키마 적용."""
    Repository.connect(get_settings().database_url).migrate()
    typer.echo("스키마 적용 완료")


@app.command("collect")
def collect_cmd(
    stocks: Annotated[list[str] | None, typer.Option("--stock", "-s", help="종목코드")] = None,
    start_year: int = 2022,
    end_year: int = 2024,
    no_documents: bool = False,
):
    """정기공시 원문과 재무제표 수집."""
    logging.basicConfig(level=logging.INFO)
    # httpx 요청 로그에는 인증키가 쿼리 문자열로 찍히므로 끈다
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    try:
        with OpenDartClient(
            settings.dart_api_key, min_interval=settings.dart_min_interval
        ) as client:
            summary = collect(
                client,
                repo,
                make_raw_store(settings),
                stocks or DEFAULT_STOCKS,
                start_year,
                end_year,
                download_documents=not no_documents,
            )
    except DartApiError as e:
        typer.echo(f"OpenDART 오류로 수집을 멈췄습니다: {e}", err=True)
        if e.status == "800":
            typer.echo("OpenDART 시스템 점검 중입니다. 점검이 끝난 뒤 다시 실행하세요.", err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"기업 {summary.companies}, 공시 {summary.filings}, "
        f"원문 신규 {summary.documents_downloaded} / 캐시 {summary.documents_cached}, "
        f"재무 항목 {summary.financial_items}"
    )
    for line in summary.skipped:
        typer.echo(f"건너뜀: {line}")
    for line in summary.errors:
        typer.echo(f"오류: {line}", err=True)
    raise typer.Exit(1 if summary.errors else 0)


@app.command()
def parse():
    """수집한 원문을 섹션·표 단위 청크로 나눠 저장."""
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    repo.migrate()
    summary = parse_filings(repo, make_raw_store(settings))
    typer.echo(f"공시 {summary.filings}건, 청크 {summary.chunks}개")
    for line in summary.errors:
        typer.echo(f"오류: {line}", err=True)
    raise typer.Exit(1 if summary.errors else 0)


def _search_backends(settings):
    from dartrag.factory import Backends

    b = Backends(settings)
    return b.embedder, b.vector, b.keyword


def _context(stocks, year_from, year_to):
    from dartrag.factory import Backends

    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    corp_codes = repo.corp_codes_for_stocks(stocks) if stocks else []
    if stocks and not corp_codes:
        typer.echo("해당 종목코드의 기업이 DB에 없습니다. 먼저 collect 를 실행하세요.", err=True)
        raise typer.Exit(1)
    flt = SearchFilter(corp_codes=corp_codes, year_from=year_from, year_to=year_to)
    return Backends(settings), flt, repo


def _retriever(stocks, year_from, year_to):
    from dartrag.factory import build_retriever

    backends, flt, repo = _context(stocks, year_from, year_to)
    return build_retriever(backends, repo), flt, repo


@app.command()
def index():
    """파싱한 청크를 임베딩해 벡터·키워드 검색 인덱스에 넣기."""
    from dartrag.pipeline.index import index_filings

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    repo.migrate()
    embedder, vector, keyword = _search_backends(settings)
    summary = index_filings(repo, embedder, vector, keyword)
    typer.echo(f"공시 {summary.filings}건, 청크 {summary.chunks}개 색인")
    for line in summary.errors:
        typer.echo(f"오류: {line}", err=True)
    raise typer.Exit(1 if summary.errors else 0)


@app.command()
def search(
    query: str,
    stocks: Annotated[list[str] | None, typer.Option("--stock", "-s", help="종목코드")] = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = 5,
):
    """하이브리드 검색 결과 확인 (답변 생성 없이 근거 청크만)."""
    retriever, flt, _ = _retriever(stocks, year_from, year_to)
    hits = retriever.search(query, flt, limit)
    if not hits:
        typer.echo("결과 없음")
    for i, h in enumerate(hits, start=1):
        c = h.chunk
        ranks = f"벡터 {h.dense_rank or '-'}위, 키워드 {h.keyword_rank or '-'}위"
        typer.echo(
            f"[{i}] {c['corp_name']} {c['report_nm']} > {' > '.join(c['section_path'])}"
            f"  (점수 {h.score:.4f}, {ranks})"
        )
        typer.echo(f"    {c['url']}")
        body = c["body"].replace("\n", " ")
        typer.echo(f"    {body[:200]}{'…' if len(body) > 200 else ''}\n")


@app.command()
def ask(
    question: str,
    stocks: Annotated[list[str] | None, typer.Option("--stock", "-s", help="종목코드")] = None,
    year_from: int | None = None,
    year_to: int | None = None,
):
    """공시를 근거로 질문에 답하기 (출처 번호 포함)."""
    from dartrag.answer import LLMError
    from dartrag.factory import build_answerer

    backends, flt, repo = _context(stocks, year_from, year_to)
    answerer = build_answerer(backends, repo)
    try:
        result = answerer.answer(question, flt)
    except LLMError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(result.text + "\n")
    for c in result.citations:
        chunk = c.hit.chunk
        typer.echo(
            f"[{c.number}] {chunk['corp_name']} {chunk['report_nm']} > "
            f"{' > '.join(chunk['section_path'])}\n    {chunk['url']}"
        )
    for w in result.warnings:
        typer.echo(f"주의: {w}", err=True)
    typer.echo("\n※ 공시 정보 요약이며 투자 권유가 아닙니다.")


@app.command()
def diff(
    stock: Annotated[str | None, typer.Option("--stock", "-s", help="종목코드")] = None,
    old: Annotated[str | None, typer.Option(help="이전 공시 접수번호")] = None,
    new: Annotated[str | None, typer.Option(help="이후 공시 접수번호")] = None,
    kind: str = "사업보고서",
    out: Path | None = None,
    summary: Annotated[
        bool, typer.Option(help="LLM 으로 새 위험·빠진 내용·주요 변경을 요약 (Ollama 필요)")
    ] = False,
    refresh: Annotated[bool, typer.Option(help="저장된 요약을 버리고 다시 만들기")] = False,
):
    """두 보고서를 섹션별로 비교해 바뀐 내용 보기 (기본: 최근 두 사업보고서)."""
    from dartrag.changes.compare import compare_filings, latest_pair

    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    if summary:
        _diff_summary(repo, settings, stock, kind, refresh)
        return
    if not (old and new):
        if not stock:
            typer.echo("--stock 또는 --old/--new 를 지정하세요.", err=True)
            raise typer.Exit(1)
        corp_codes = repo.corp_codes_for_stocks([stock])
        pair = latest_pair(repo, corp_codes[0], kind) if corp_codes else None
        if pair is None:
            typer.echo(
                f"비교할 {kind}가 두 건 이상 없습니다. collect·parse 를 먼저 실행하세요.", err=True
            )
            raise typer.Exit(1)
        old, new = pair
    try:
        result = compare_filings(repo, old, new)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    text = result.markdown()
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"→ {out}")
    else:
        typer.echo(text)


def _diff_summary(repo, settings, stock, kind, refresh):
    from dartrag.answer import LLMError
    from dartrag.changes.summary import latest_digest, render_text
    from dartrag.factory import build_llm

    corp_codes = repo.corp_codes_for_stocks([stock]) if stock else []
    if not corp_codes:
        typer.echo("--stock 으로 DB 에 있는 종목코드를 지정하세요.", err=True)
        raise typer.Exit(1)
    try:
        digest = latest_digest(repo, corp_codes[0], build_llm(settings), kind, refresh=refresh)
    except (LLMError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if digest is None:
        typer.echo(f"비교할 {kind}가 두 건 이상 없습니다.", err=True)
        raise typer.Exit(1)
    typer.echo(render_text(digest))
    if digest.dropped:
        typer.echo(f"참고: 근거 번호가 틀린 요약 문장 {digest.dropped}개를 뺐습니다.", err=True)


@app.command()
def report(
    stock: Annotated[str, typer.Option("--stock", "-s", help="종목코드")],
    out: Annotated[Path | None, typer.Option("--out", "-o", help="저장할 PDF 경로")] = None,
    llm: Annotated[bool, typer.Option(help="사업 개요·위험 요인 요약 넣기 (Ollama 필요)")] = True,
):
    """기업 리포트 PDF 만들기 (재무 추이, 분기 실적, 요약, 변경점, 최근 공시)."""
    from dartrag.factory import Backends, build_answerer
    from dartrag.report import build_report, render_pdf

    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    answerer = build_answerer(Backends(settings), repo) if llm else None
    try:
        data = build_report(repo, stock, answerer=answerer)
    except LookupError as e:
        typer.echo(f"{e}. collect 를 먼저 실행하세요.", err=True)
        raise typer.Exit(1) from None
    out = out or Path(f"report_{stock}_{data.generated_at:%Y%m%d}.pdf")
    out.write_bytes(render_pdf(data, settings.report_font))
    for note in data.notes:
        typer.echo(f"참고: {note}", err=True)
    typer.echo(f"→ {out}")


feed_app = typer.Typer(help="주요 공시 피드와 알림")
app.add_typer(feed_app, name="feed")
watch_app = typer.Typer(help="알림 받을 관심 종목")
app.add_typer(watch_app, name="watch")


def _send_all_alerts(repo, settings) -> tuple[int, list[str]]:
    """운영자 채널(.env)과 사용자별 채널(웹에서 등록)로 알림을 보낸다."""
    from dartrag.factory import build_notifiers, build_senders
    from dartrag.feed.alerts import send_user_alerts, unsubscribe_link
    from dartrag.pipeline.feed import send_alerts

    sent, errors = 0, []
    for notifier in build_notifiers(settings, typer.echo):
        n, errs = send_alerts(repo, notifier)
        sent, errors = sent + n, errors + errs
    run = send_user_alerts(
        repo,
        build_senders(settings),
        unsubscribe_url=unsubscribe_link(settings.public_url, settings.secret_key),
    )
    return sent + run.sent, errors + run.errors


@feed_app.command("poll")
def feed_poll(
    days: int = 1,
    every: Annotated[int, typer.Option(help="초 단위 반복 간격. 0 이면 한 번만")] = 0,
    all_companies: bool = False,
):
    """최근 공시를 받아 분류·저장하고 관심 종목 알림 보내기."""
    import time
    from datetime import date, timedelta

    from dartrag.pipeline.feed import poll

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    repo.migrate()
    with OpenDartClient(settings.dart_api_key, min_interval=settings.dart_min_interval) as client:
        while True:
            today = date.today()
            try:
                s = poll(
                    client,
                    repo,
                    today - timedelta(days=max(days - 1, 0)),
                    today,
                    listed_only=not all_companies,
                )
                sent, errors = _send_all_alerts(repo, settings)
                typer.echo(f"공시 {s.fetched}건 확인, 새 공시 {s.new}건, 알림 {sent}건")
                for e in errors:
                    typer.echo(f"알림 오류: {e}", err=True)
            except DartApiError as e:
                typer.echo(f"OpenDART 오류: {e}", err=True)
                if not every:
                    raise typer.Exit(1) from None
            if not every:
                break
            time.sleep(every)


telegram_app = typer.Typer(help="텔레그램 알림 봇")
app.add_typer(telegram_app, name="telegram")


def _telegram(settings):
    from dartrag.feed.channels import TelegramSender

    if not settings.telegram_bot_token:
        typer.echo(".env 에 TELEGRAM_BOT_TOKEN 을 넣으세요 (@BotFather 에서 발급).", err=True)
        raise typer.Exit(1)
    return TelegramSender(settings.telegram_bot_token)


@telegram_app.command("poll")
def telegram_poll():
    """봇에 온 메시지를 받아 사용자 연결을 처리 (내 컴퓨터에서 웹훅 없이 쓸 때). Ctrl+C 로 종료."""
    from dartrag.feed.channels import SendError
    from dartrag.feed.telegram_bot import handle_update

    settings = get_settings()
    sender = _telegram(settings)
    repo = Repository.connect(settings.database_url)
    offset = None
    typer.echo("텔레그램 메시지를 기다립니다...")
    while True:
        try:
            updates = sender.get_updates(offset)
        except SendError as e:
            typer.echo(str(e), err=True)
            import time

            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            reply = handle_update(repo, u, sender)
            if reply:
                typer.echo(f"처리: {reply.splitlines()[0]}")


@telegram_app.command("webhook")
def telegram_webhook():
    """공개 서버에서 텔레그램이 PUBLIC_URL/api/telegram/webhook 으로 메시지를 보내게 등록."""
    from dartrag.feed.channels import SendError

    settings = get_settings()
    if not settings.telegram_webhook_secret or not settings.public_url.startswith("https://"):
        typer.echo("TELEGRAM_WEBHOOK_SECRET 과 https 주소의 PUBLIC_URL 이 필요합니다.", err=True)
        raise typer.Exit(1)
    try:
        _telegram(settings).set_webhook(
            f"{settings.public_url.rstrip('/')}/api/telegram/webhook",
            settings.telegram_webhook_secret,
        )
    except SendError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo("웹훅을 등록했습니다.")


jobs_app = typer.Typer(help="백그라운드 작업 (보통은 Celery 작업자가 자동으로 실행)")
app.add_typer(jobs_app, name="jobs")
JOB_NAMES = (
    "feed_poll",
    "ingest",
    "process",
    "send_alerts",
    "backfill",
    "validate",
    "maintenance",
    "evaluate",
)


@jobs_app.command("run")
def jobs_run(
    name: Annotated[str, typer.Argument(help=" / ".join(JOB_NAMES))],
    stocks: Annotated[list[str] | None, typer.Option("--stock", "-s", help="process 대상")] = None,
):
    """작업 하나를 지금 바로 실행 (Celery 없이)."""
    import json

    from dartrag.worker import jobs

    if name not in JOB_NAMES:
        typer.echo(f"작업 이름은 {', '.join(JOB_NAMES)} 중 하나입니다.", err=True)
        raise typer.Exit(1)
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    ctx = jobs.Context.from_settings(settings)
    kwargs = {}
    if name == "process" and stocks:
        with ctx.repo() as repo:
            kwargs["corp_codes"] = repo.corp_codes_for_stocks(stocks)
    try:
        result = jobs.run_job(ctx, name, lambda c, r: getattr(jobs, name)(c, r, **kwargs))
    except jobs.JobSkipped:
        typer.echo(f"{name} 이(가) 이미 실행 중입니다.", err=True)
        raise typer.Exit(1) from None
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@jobs_app.command("status")
def jobs_status():
    """작업별 마지막 실행, 처리 대기 중인 보고서, 과거 데이터 진행률, 오늘 쓴 OpenDART 호출 수."""
    from dartrag.worker import jobs

    settings = get_settings()
    ctx = jobs.Context.from_settings(settings)
    with ctx.repo() as repo:
        runs = repo.last_job_runs()
        backlog = repo.ingest_backlog()
        progress = repo.backfill_progress()
    for name in JOB_NAMES:
        r = runs.get(name)
        if r is None:
            typer.echo(f"{name:12} 실행 기록 없음")
            continue
        last_ok = f"{r['last_ok']:%m-%d %H:%M}" if r["last_ok"] else "-"
        typer.echo(
            f"{name:12} 마지막 {r['last_started']:%m-%d %H:%M} ({r['last_status']})"
            f" · 마지막 성공 {last_ok}"
        )
    typer.echo(f"\n새 정기보고서 처리 대기 {backlog['pending']}건, 멈춘 것 {backlog['failed']}건")
    total = sum(progress.values())
    if total:
        typer.echo(f"과거 데이터: {progress['done']}/{total}곳 완료, 오류 {progress['error']}곳")
    quota = ctx.quota()
    if quota is not None:
        typer.echo(f"오늘 OpenDART 호출: {quota.used():,}/{quota.limit:,}회")


@app.command()
def validate(
    stocks: Annotated[list[str] | None, typer.Option("--stock", "-s", help="종목코드")] = None,
):
    """재무 데이터 검증 (자산 = 부채 + 자본, 급변, 누락, 단위)."""
    from dartrag.finance.validate import run_validation

    repo = Repository.connect(get_settings().database_url)
    codes = repo.corp_codes_for_stocks(stocks) if stocks else None
    counts = run_validation(repo, codes)
    typer.echo(f"{counts['companies']}곳 검사: 오류 {counts['error']}건, 경고 {counts['warn']}건")
    for i in [r for c in (codes or [None]) for r in repo.data_issues(c)][:50]:
        mark = "✗" if i["severity"] == "error" else "!"
        typer.echo(
            f"{mark} {i['corp_name'] or i['corp_code']} {i['bsns_year']} {i['reprt_code']}"
            f" {i['fs_div']} [{i['rule']}] {i['detail']}"
        )


alert_app = typer.Typer(help="알림 채널 점검")
app.add_typer(alert_app, name="alert")


@alert_app.command("test")
def alert_test():
    """.env 에 설정한 내 알림 채널(웹훅·텔레그램·이메일)로 시험 메시지 보내기."""
    from dartrag.factory import build_notifiers

    failed = False
    for n in build_notifiers(get_settings(), typer.echo):
        try:
            n.send("DART 공시 알림 시험 메시지입니다.")
            typer.echo(f"✓ {n.channel}")
        except Exception as e:  # noqa: BLE001
            failed = True
            typer.echo(f"✗ {n.channel}: {e}", err=True)
    if failed:
        raise typer.Exit(1)


@feed_app.command("show")
def feed_show(
    days: int = 7,
    min_importance: Annotated[int, typer.Option(min=1, max=3)] = 2,
    stocks: Annotated[list[str] | None, typer.Option("--stock", "-s", help="종목코드")] = None,
):
    """저장된 주요 공시 보기 (중요도 3 🔴, 2 🟠, 1 ⚪)."""
    from datetime import date, timedelta

    from dartrag.feed.notify import IMPORTANCE_MARK

    repo = Repository.connect(get_settings().database_url)
    corp_codes = repo.corp_codes_for_stocks(stocks) if stocks else None
    rows = repo.recent_disclosures(date.today() - timedelta(days=days), min_importance, corp_codes)
    if not rows:
        typer.echo("해당 기간에 공시가 없습니다. dartrag feed poll 을 먼저 실행하세요.")
    for d in rows:
        corr = " (정정)" if d["correction"] else ""
        typer.echo(
            f"{d['rcept_dt']} {IMPORTANCE_MARK[d['importance']]} {d['corp_name']}"
            f" · {d['event_label']}{corr} | {d['report_nm']}"
        )


@watch_app.command("add")
def watch_add(
    stocks: list[str],
    min_importance: Annotated[int, typer.Option(min=1, max=3)] = 2,
):
    """관심 종목 추가 (기본: 중요도 2 이상 공시만 알림)."""
    repo = Repository.connect(get_settings().database_url)
    repo.migrate()
    for stock in stocks:
        codes = repo.corp_codes_for_stocks([stock])
        if not codes:
            typer.echo(f"{stock}: DB에 없는 종목입니다. collect 를 먼저 실행하세요.", err=True)
            continue
        repo.set_watch(codes[0], min_importance)
        typer.echo(f"{stock} 추가 (중요도 {min_importance} 이상 알림)")


@watch_app.command("remove")
def watch_remove(stocks: list[str]):
    """관심 종목 삭제."""
    repo = Repository.connect(get_settings().database_url)
    for stock in stocks:
        codes = repo.corp_codes_for_stocks([stock])
        removed = bool(codes) and repo.remove_watch(codes[0])
        typer.echo(f"{stock} {'삭제' if removed else '목록에 없음'}")


@watch_app.command("list")
def watch_list():
    """관심 종목 목록."""
    rows = Repository.connect(get_settings().database_url).watchlist()
    if not rows:
        typer.echo("관심 종목이 없습니다. dartrag watch add 005930 처럼 추가하세요.")
    for _code, name, stock, imp in rows:
        typer.echo(f"{stock or '-'} {name} (중요도 {imp} 이상)")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """웹 화면과 API 서버 실행 (기본: http://127.0.0.1:8000)."""
    import uvicorn

    settings = get_settings()
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.auth_required:
        typer.echo(
            "주의: 다른 컴퓨터에서 접속할 수 있게 열었는데 로그인이 꺼져 있습니다. "
            "공개하려면 .env 에 AUTH_REQUIRED=true 를 넣으세요.",
            err=True,
        )
    typer.echo(f"http://{host}:{port} 에서 열립니다")
    uvicorn.run("dartrag.web.asgi:app", host=host, port=port, reload=reload)


eval_app = typer.Typer(help="답변 품질 평가")
app.add_typer(eval_app, name="eval")


@eval_app.command("generate")
def eval_generate(
    out: Path = Path("eval/generated.jsonl"),
    per_company: int = 8,
    seed: int = 0,
):
    """재무 DB 에서 정답이 있는 숫자 문항을 자동 생성."""
    from dartrag.eval import save_cases
    from dartrag.eval.generate import generate_cases, load_values

    repo = Repository.connect(get_settings().database_url)
    companies = repo.listed_companies()
    values = load_values(repo, [c for c, _ in companies])
    cases = generate_cases(companies, values, per_company=per_company, seed=seed)
    if not cases:
        typer.echo("재무 데이터가 없습니다. 먼저 collect 를 실행하세요.", err=True)
        raise typer.Exit(1)
    save_cases(out, cases)
    typer.echo(f"{len(cases)}문항 → {out}")


@eval_app.command("run")
def eval_run(
    files: list[Path],
    out: Path = Path("reports/eval"),
    limit: int | None = None,
    min_pass_rate: float | None = None,
    gate: Annotated[
        bool, typer.Option(help="배포 기준(eval/release_criteria.toml) 미달이면 실패")
    ] = False,
):
    """평가 문항으로 검색·답변을 실행하고 채점 리포트와 배포 기준 판정을 작성."""
    from dartrag.answer.prompt import PROMPT_VERSION
    from dartrag.eval import load_cases
    from dartrag.eval.service import run_and_record
    from dartrag.factory import build_answerer

    settings = get_settings()
    cases = load_cases(*files)[:limit]
    backends, _, repo = _context(None, None, None)
    # 평가는 매번 실제로 답을 만들어야 하므로 캐시를 쓰지 않는다
    answerer = build_answerer(backends, repo, use_cache=False)

    def progress(i, g):
        mark = "통과" if g.passed else "실패: " + "; ".join(g.reasons)
        typer.echo(f"[{i}/{len(cases)}] {g.case_id} {mark}")

    meta = {
        "llm": settings.llm_model,
        "embed": settings.embed_model,
        "rerank": settings.rerank_model or "-",
        "prompt": PROMPT_VERSION,
        "cases": len(cases),
        "files": ", ".join(str(f) for f in files),
    }
    result = run_and_record(repo, answerer, cases, out, meta, on_progress=progress)
    rate = result["summary"]["overall"]["pass_rate"] or 0.0
    typer.echo(f"통과율 {rate:.1%} → {result['report']}")
    typer.echo("\n" + result["gate"])
    if min_pass_rate is not None and rate < min_pass_rate:
        typer.echo(f"기준 통과율 {min_pass_rate:.0%} 미달", err=True)
        raise typer.Exit(1)
    if gate and not result["passed"]:
        raise typer.Exit(1)
    typer.echo(f"\n이 결과를 새 기준선으로 쓰려면: dartrag eval baseline {result['summary_path']}")


@eval_app.command("gate")
def eval_gate(summary_path: Path, baseline: Path = Path("eval/baseline.json")):
    """평가 요약(summary.json)이 배포 기준을 넘는지 확인."""
    import json

    from dartrag.eval.gate import check_release, load_baseline, load_criteria, render_checks

    summary = json.loads(summary_path.read_text("utf-8"))
    checks = check_release(summary, load_criteria(), load_baseline(baseline))
    typer.echo(render_checks(checks))
    if not all(c.ok for c in checks):
        raise typer.Exit(1)


@eval_app.command("baseline")
def eval_baseline(summary_path: Path, out: Path = Path("eval/baseline.json")):
    """평가 요약을 기준선으로 저장 (저장소에 함께 올리면 CI 가 이것으로 검사)."""
    import json

    from dartrag.eval.gate import check_release, load_criteria

    summary = json.loads(summary_path.read_text("utf-8"))
    failed = [c.name for c in check_release(summary, load_criteria()) if not c.ok]
    if failed:
        typer.echo(f"배포 기준 미달이라 기준선으로 쓸 수 없습니다: {', '.join(failed)}", err=True)
        raise typer.Exit(1)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    typer.echo(f"기준선 저장 → {out}")


@eval_app.command("ci-check")
def eval_ci_check():
    """CI 용: eval/baseline.json 이 배포 기준을 넘고 현재 프롬프트로 잰 것인지 확인."""
    from dartrag.answer.prompt import PROMPT_VERSION
    from dartrag.eval.gate import ci_check

    ok, text = ci_check(prompt_version=PROMPT_VERSION)
    typer.echo(text)
    if not ok:
        raise typer.Exit(1)


@eval_app.command("promote")
def eval_promote(
    candidates: Path = Path("eval/feedback_candidates.jsonl"),
    target: Path = Path("eval/manual.jsonl"),
):
    """정답을 채운 사용자 평가 후보 문항을 평가셋으로 옮김."""
    from dartrag.eval.service import promote_reviewed

    moved, left = promote_reviewed(candidates, target)
    typer.echo(f"{moved}문항을 {target} 로 옮김. 검수할 후보 {left}문항 남음")


@app.command()
def doctor(offline: bool = False):
    """실행 전 준비 상태 점검 (인증키, Docker 서비스, 검색, Ollama)."""
    from dartrag.doctor import all_ok, format_checks, run_checks

    logging.getLogger("httpx").setLevel(logging.WARNING)
    checks = run_checks(get_settings(), online=not offline)
    typer.echo(format_checks(checks))
    if all_ok(checks):
        typer.echo("\n준비 완료. dartrag run 으로 수집부터 평가까지 한 번에 실행할 수 있습니다.")
        return
    typer.echo("\n❌ 표시를 위에서부터 고친 뒤 dartrag doctor 를 다시 실행하세요.", err=True)
    raise typer.Exit(1)


def _step(name: str, fn, *args, **kwargs) -> tuple[str, int, float]:
    import time

    typer.echo(f"\n━━ {name} ━━")
    started = time.monotonic()
    try:
        fn(*args, **kwargs)
        code = 0
    except typer.Exit as e:
        code = e.exit_code
    return name, code, time.monotonic() - started


@app.command("run")
def run_all(
    stocks: Annotated[list[str] | None, typer.Option("--stock", "-s", help="종목코드")] = None,
    start_year: int = 2022,
    end_year: int = 2024,
    feed_days: int = 30,
    eval_limit: Annotated[int, typer.Option(help="평가 문항 수. 0 이면 평가 생략")] = 40,
):
    """준비 점검 → 수집 → 파싱 → 색인 → 공시 피드 → 평가를 차례로 실행."""
    from dartrag.doctor import all_ok, format_checks, run_checks

    logging.getLogger("httpx").setLevel(logging.WARNING)
    checks = run_checks(get_settings())
    typer.echo(format_checks(checks))
    if not all_ok(checks):
        typer.echo("\n준비가 덜 됐습니다. ❌ 항목을 고친 뒤 다시 실행하세요.", err=True)
        raise typer.Exit(1)

    results = [
        _step("1. 수집", collect_cmd, stocks, start_year, end_year, False),
        _step("2. 파싱", parse),
        _step("3. 색인", index),
        _step("4. 공시 피드", feed_poll, feed_days, 0, False),
    ]
    if eval_limit:
        generated = Path("eval/generated.jsonl")
        results.append(_step("5. 평가 문항 생성", eval_generate, generated, 8, 0))
        files = [Path("eval/manual.jsonl")] + ([generated] if generated.exists() else [])
        results.append(_step("6. 평가", eval_run, files, Path("reports/eval"), eval_limit, None))

    typer.echo("\n━━ 요약 ━━")
    for name, code, secs in results:
        typer.echo(f"{'✅' if code == 0 else '❌'} {name} ({secs / 60:.1f}분)")
    failed = [name for name, code, _ in results if code]
    if failed:
        typer.echo("일부 단계에 오류가 있습니다. 위 로그의 '오류:' 줄을 확인하세요.", err=True)
    typer.echo("웹 화면은 dartrag serve 로 열 수 있습니다.")
    raise typer.Exit(1 if failed else 0)


user_app = typer.Typer(help="로그인 계정 관리 (AUTH_REQUIRED=true 일 때 사용)")
app.add_typer(user_app, name="user")


def _ask_password() -> str:
    from dartrag.web.auth import AuthError, check_password_policy

    password = typer.prompt("비밀번호", hide_input=True, confirmation_prompt="비밀번호 확인")
    try:
        check_password_policy(password)
    except AuthError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    return password


@user_app.command("add")
def user_add(email: str):
    """계정 만들기. 비밀번호는 화면에 보이지 않게 입력받습니다."""
    from dartrag.web.auth import AuthError, hash_password, normalize_email

    try:
        email = normalize_email(email)
    except AuthError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    password = _ask_password()
    repo = Repository.connect(get_settings().database_url)
    repo.migrate()
    if repo.create_user(email, hash_password(password)) is None:
        typer.echo(f"{email} 은 이미 있는 계정입니다.", err=True)
        raise typer.Exit(1)
    typer.echo(f"{email} 계정을 만들었습니다.")


@user_app.command("password")
def user_password(email: str):
    """비밀번호 바꾸기 (그 계정의 모든 로그인이 끊깁니다)."""
    repo = Repository.connect(get_settings().database_url)
    row = repo.user_by_email(email.strip().lower())
    if row is None:
        typer.echo("없는 계정입니다.", err=True)
        raise typer.Exit(1)
    from dartrag.web.auth import hash_password

    repo.set_password(row[0], hash_password(_ask_password()))
    typer.echo("비밀번호를 바꿨습니다.")


@user_app.command("list")
def user_list():
    """계정 목록."""
    rows = Repository.connect(get_settings().database_url).users()
    if not rows:
        typer.echo("계정이 없습니다. dartrag user add 이메일 로 만드세요.")
    for _id, email, created, last in rows:
        last_s = last.strftime("%Y-%m-%d %H:%M") if last else "-"
        typer.echo(f"{email}  가입 {created:%Y-%m-%d}  마지막 로그인 {last_s}")


@user_app.command("remove")
def user_remove(email: str):
    """계정 삭제 (관심 종목, 로그인 기록도 함께 삭제)."""
    removed = Repository.connect(get_settings().database_url).remove_user(email.strip().lower())
    typer.echo(f"{email} {'삭제' if removed else '없는 계정'}")


feedback_app = typer.Typer(help="사용자 답변 평가")
app.add_typer(feedback_app, name="feedback")


@feedback_app.command("stats")
def feedback_stats():
    """👍/👎 개수와 👎 사유별 개수."""
    from collections import Counter

    from dartrag.eval.feedback import REASON_LABEL

    rows = Repository.connect(get_settings().database_url).feedback_rows()
    if not rows:
        typer.echo("아직 평가가 없습니다.")
        return
    up = sum(1 for r in rows if r["rating"] == 1)
    typer.echo(f"👍 {up}  👎 {len(rows) - up}  (👍 비율 {up / len(rows):.0%})")
    for reason, n in Counter(r["reason"] for r in rows if r["rating"] == -1).most_common():
        typer.echo(f"  {REASON_LABEL.get(reason or '', '사유 없음')}: {n}")


@feedback_app.command("export")
def feedback_export(out: Path = Path("eval/feedback_candidates.jsonl")):
    """👎 받은 질문을 평가셋 후보에 추가 (정답을 채운 뒤 dartrag eval promote 로 옮김).

    이미 후보에 있거나 평가셋으로 옮긴 질문은 다시 넣지 않는다."""
    from dartrag.eval import load_cases, save_cases
    from dartrag.eval.feedback import feedback_to_cases

    rows = Repository.connect(get_settings().database_url).feedback_rows(rating=-1)
    existing = load_cases(out) if out.exists() else []
    manual = Path("eval/manual.jsonl")
    known = {c.id for c in existing} | (
        {c.id for c in load_cases(manual)} if manual.exists() else set()
    )
    new = [c for c in feedback_to_cases(rows) if c.id not in known]
    if not new:
        typer.echo("새로 추가할 👎 평가가 없습니다.")
        return
    save_cases(out, existing + new)
    typer.echo(f"{len(new)}문항 추가 → {out} (후보 {len(existing) + len(new)}문항)")


if __name__ == "__main__":
    app()
