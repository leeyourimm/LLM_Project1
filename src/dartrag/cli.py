import logging
from typing import Annotated

import typer

from dartrag.config import get_settings
from dartrag.dart import DartApiError, OpenDartClient
from dartrag.db import Repository
from dartrag.pipeline.collect import collect
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


if __name__ == "__main__":
    app()
