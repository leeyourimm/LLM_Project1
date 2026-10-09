"""답변 신뢰도 표시에 쓰는 사실 모음. 점수를 만들지 않고 서비스가 실제로 확인한 것만 모은다.

- 답변이 인용한 서로 다른 출처(청크) 수와, 그 출처들이 속한 서로 다른 공시 수
- 인용한 공시 중 가장 최근 것이 그 회사의 최신 정기공시인지, 아니면 얼마나 오래됐는지
  (정정공시도 따로 접수되므로 원본을 인용했는데 정정본이 있으면 최신이 아니다)
- 검증기가 이미 계산한 결과: 원문과 대조한 숫자 수, 확인되지 않은 숫자, 출처 번호 문제

화면 문구는 프런트엔드(frontend/src/lib/trust.ts)가 만든다.
"""

from collections.abc import Callable
from datetime import date


def _filings_of(hit) -> list[str]:
    """출처 하나가 기대는 공시 접수번호들. 재무 데이터 출처는 표에 쓴 보고서 전부."""
    c = hit.chunk
    nos = c.get("rcept_nos") or ([c["rcept_no"]] if c.get("rcept_no") else [])
    return [n for n in nos if n]


def cited_filings(answer) -> list[str]:
    seen: dict[str, None] = {}
    for c in answer.citations:
        for no in _filings_of(c.hit):
            seen.setdefault(no, None)
    return list(seen)


def _date_from_rcept_no(rcept_no: str) -> date | None:
    """DART 접수번호 앞 8자리는 접수일(YYYYMMDD). DB 에 없는 공시의 날짜를 알 때 쓴다."""
    try:
        return date(int(rcept_no[:4]), int(rcept_no[4:6]), int(rcept_no[6:8]))
    except (ValueError, TypeError):
        return None


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def assess(answer, freshness: Callable[[list[str]], dict], today: date) -> dict | None:
    """답변 하나의 신뢰도 표시용 사실. 거절했거나 답을 못 찾은 답변은 None.

    freshness: 접수번호들 → {접수번호: {corp_code, corp_name, report_nm, rcept_dt, latest}}
               (Repository.filing_freshness)"""
    if answer.refused or not answer.found:
        return None
    filings = cited_filings(answer)
    known = freshness(filings) if filings else {}

    # 회사마다 인용한 공시 중 가장 최근 것 → 그 회사의 최신 정기공시와 비교
    newest_by_corp: dict[str, dict] = {}
    for f in known.values():
        cur = newest_by_corp.get(f["corp_code"])
        if cur is None or (f["rcept_dt"], f["rcept_no"]) > (cur["rcept_dt"], cur["rcept_no"]):
            newest_by_corp[f["corp_code"]] = f
    companies = []
    for f in sorted(newest_by_corp.values(), key=lambda f: f["corp_name"]):
        latest = f["latest"]
        # 비교할 정기공시가 없으면 모른다(None)
        is_latest = None
        if latest is not None:
            is_latest = (f["rcept_dt"], f["rcept_no"]) >= (latest["rcept_dt"], latest["rcept_no"])
        companies.append(
            {
                "corp_code": f["corp_code"],
                "corp_name": f["corp_name"],
                "cited": {
                    "rcept_no": f["rcept_no"],
                    "report_nm": f["report_nm"],
                    "rcept_dt": _iso(f["rcept_dt"]),
                },
                "latest": None
                if latest is None
                else {
                    "rcept_no": latest["rcept_no"],
                    "report_nm": latest["report_nm"],
                    "rcept_dt": _iso(latest["rcept_dt"]),
                    "indexed": latest["indexed"],
                },
                "is_latest": is_latest,
            }
        )

    # 인용한 공시 중 가장 최근 접수일 (DB 에 없는 공시는 접수번호의 날짜로)
    dates = [
        (known[no]["rcept_dt"] if no in known else _date_from_rcept_no(no), no) for no in filings
    ]
    dates = [(d, no) for d, no in dates if d]
    newest = None
    if dates:
        d, no = max(dates)
        f = known.get(no, {})
        newest = {
            "rcept_no": no,
            "rcept_dt": d.isoformat(),
            "report_nm": f.get("report_nm"),
            "corp_name": f.get("corp_name"),
            "age_days": (today - d).days,
        }
    return {
        "sources": len(answer.citations),
        "filings": len(filings),
        "numbers_checked": answer.numbers_checked,
        "unverified_numbers": list(answer.unverified),
        "invalid_citations": list(answer.invalid_citations),
        "uncited": not answer.citations,
        "newest": newest,
        "companies": companies,
        "checked_on": today.isoformat(),
    }
