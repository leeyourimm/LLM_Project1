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
    from qdrant_client import QdrantClient

    from dartrag.search import KeywordIndex, SentenceTransformerEmbedder, VectorIndex

    return (
        SentenceTransformerEmbedder(settings.embed_model),
        VectorIndex(QdrantClient(url=settings.qdrant_url)),
        KeywordIndex.connect(settings.opensearch_url),
    )


def _retriever(stocks, year_from, year_to):
    from dartrag.search import HybridRetriever

    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    corp_codes = repo.corp_codes_for_stocks(stocks) if stocks else []
    if stocks and not corp_codes:
        typer.echo("해당 종목코드의 기업이 DB에 없습니다. 먼저 collect 를 실행하세요.", err=True)
        raise typer.Exit(1)
    embedder, vector, keyword = _search_backends(settings)
    retriever = HybridRetriever(embedder, vector, keyword, repo.get_chunks)
    flt = SearchFilter(corp_codes=corp_codes, year_from=year_from, year_to=year_to)
    return retriever, flt, repo


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
    from dartrag.answer import Answerer, LLMError, OllamaLLM
    from dartrag.finance import FinanceTool

    settings = get_settings()
    retriever, flt, repo = _retriever(stocks, year_from, year_to)
    answerer = Answerer(
        retriever,
        OllamaLLM(settings.llm_model, settings.ollama_url),
        finance=FinanceTool(repo),
    )
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
):
    """두 보고서를 섹션별로 비교해 바뀐 내용 보기 (기본: 최근 두 사업보고서)."""
    from dartrag.changes.compare import compare_filings, latest_pair

    repo = Repository.connect(get_settings().database_url)
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


feed_app = typer.Typer(help="주요 공시 피드와 알림")
app.add_typer(feed_app, name="feed")
watch_app = typer.Typer(help="알림 받을 관심 종목")
app.add_typer(watch_app, name="watch")


def _notifier(settings):
    from dartrag.feed.notify import ConsoleNotifier, WebhookNotifier

    if settings.alert_webhook_url:
        return WebhookNotifier(settings.alert_webhook_url)
    return ConsoleNotifier(typer.echo)


@feed_app.command("poll")
def feed_poll(
    days: int = 1,
    every: Annotated[int, typer.Option(help="초 단위 반복 간격. 0 이면 한 번만")] = 0,
    all_companies: bool = False,
):
    """최근 공시를 받아 분류·저장하고 관심 종목 알림 보내기."""
    import time
    from datetime import date, timedelta

    from dartrag.pipeline.feed import poll, send_alerts

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    repo.migrate()
    notifier = _notifier(settings)
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
                sent, errors = send_alerts(repo, notifier)
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
def serve(host: str = "127.0.0.1", port: int = 8000):
    """웹 화면과 API 서버 실행 (기본: http://127.0.0.1:8000)."""
    import uvicorn

    from dartrag.web import create_app, default_services

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    repo = Repository.connect(settings.database_url)
    repo.migrate()
    repo.conn.close()
    typer.echo(f"http://{host}:{port} 에서 열립니다")
    uvicorn.run(create_app(default_services(settings)), host=host, port=port)


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
):
    """평가 문항으로 검색·답변을 실행하고 채점 리포트 작성."""
    from datetime import datetime

    from dartrag.answer import Answerer, OllamaLLM
    from dartrag.eval import load_cases, run_eval, summarize, write_report
    from dartrag.finance import FinanceTool

    settings = get_settings()
    cases = load_cases(*files)[:limit]
    retriever, _, repo = _retriever(None, None, None)
    llm = OllamaLLM(settings.llm_model, settings.ollama_url)
    answerer = Answerer(retriever, llm, finance=FinanceTool(repo))

    def progress(i, g):
        mark = "통과" if g.passed else "실패: " + "; ".join(g.reasons)
        typer.echo(f"[{i}/{len(cases)}] {g.case_id} {mark}")

    grades = run_eval(cases, answerer.answer, repo.corp_codes_for_stocks, progress)
    run_dir = out / datetime.now().strftime("%Y%m%d-%H%M%S")
    meta = {
        "llm": settings.llm_model,
        "embed": settings.embed_model,
        "cases": len(cases),
        "files": ", ".join(str(f) for f in files),
    }
    report = write_report(run_dir, grades, meta)
    rate = summarize(grades)["overall"]["pass_rate"] or 0.0
    typer.echo(f"통과율 {rate:.1%} → {report}")
    if min_pass_rate is not None and rate < min_pass_rate:
        typer.echo(f"기준 통과율 {min_pass_rate:.0%} 미달", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
