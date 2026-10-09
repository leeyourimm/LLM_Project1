"""DB 의 청크로 두 공시를 비교."""

from dataclasses import dataclass

from dartrag.changes.diff import SectionDiff, diff_reports, render_markdown, section_key


@dataclass
class Comparison:
    old: dict  # rcept_no, report_nm, rcept_dt
    new: dict
    diffs: list[SectionDiff]

    @property
    def title(self) -> str:
        return f"{self.new['corp_name']} 변경점: {self.old['report_nm']} → {self.new['report_nm']}"

    def markdown(self) -> str:
        links = (
            f"이전: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.old['rcept_no']}  \n"
            f"이후: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.new['rcept_no']}\n\n"
        )
        body = render_markdown(self.diffs, self.title)
        head, _, rest = body.partition("\n\n")
        return f"{head}\n\n{links}{rest}"


def sections_of(rows) -> dict[str, list[str]]:
    """(source_file, ord, section_path, body) 행 → 섹션 키별 본문. 본문 문서만 쓴다."""
    rows = list(rows)
    main = min((r[0] for r in rows), key=len, default=None)  # 첨부보다 본문 파일명이 짧다
    out: dict[str, list[str]] = {}
    for source_file, _ord, path, body in sorted(rows, key=lambda r: (r[0], r[1])):
        if source_file != main:
            continue
        out.setdefault(section_key(path), []).append(body)
    return out


def compare_filings(repo, old_rcept_no: str, new_rcept_no: str) -> Comparison:
    old, new = repo.filing_info(old_rcept_no), repo.filing_info(new_rcept_no)
    for rcept_no, info in ((old_rcept_no, old), (new_rcept_no, new)):
        if info is None:
            raise ValueError(f"공시 {rcept_no} 가 DB 에 없습니다")
        if not info["parsed"]:
            raise ValueError(
                f"공시 {rcept_no} 를 아직 파싱하지 않았습니다. dartrag parse 를 먼저 실행하세요"
            )
    diffs = diff_reports(
        sections_of(repo.chunk_rows(old_rcept_no)), sections_of(repo.chunk_rows(new_rcept_no))
    )
    return Comparison(old, new, diffs)


def latest_pair(repo, corp_code: str, report_kind: str = "사업보고서") -> tuple[str, str] | None:
    """가장 최근 두 기간의 보고서 (정정공시가 있으면 기간별 마지막 접수본)."""
    periods = repo.latest_filing_per_period(corp_code, report_kind)
    if len(periods) < 2:
        return None
    return periods[-2], periods[-1]
