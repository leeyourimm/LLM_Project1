import logging
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


if __name__ == "__main__":
    app()
