"""정기보고서 이름 해석과 보고서 코드."""

import re
from dataclasses import dataclass
from enum import StrEnum


class ReportCode(StrEnum):
    """fnlttSinglAcntAll 의 reprt_code."""

    Q1 = "11013"
    HALF = "11012"
    Q3 = "11014"
    ANNUAL = "11011"


@dataclass(frozen=True)
class PeriodicReport:
    kind: str  # 사업보고서 / 반기보고서 / 분기보고서
    period_year: int
    period_month: int
    is_correction: bool

    def reprt_code(self, fiscal_end_month: int = 12) -> ReportCode:
        """보고 기간이 사업연도 시작부터 몇 개월째인지로 보고서 코드를 정한다."""
        months = (self.period_month - fiscal_end_month) % 12 or 12
        return {3: ReportCode.Q1, 6: ReportCode.HALF, 9: ReportCode.Q3, 12: ReportCode.ANNUAL}[
            months
        ]

    @property
    def period_key(self) -> str:
        return f"{self.period_year:04d}.{self.period_month:02d}"


_REPORT_RE = re.compile(
    r"^(?P<prefix>(\[[^\]]+\]\s*)*)(?P<kind>사업보고서|반기보고서|분기보고서)\s*"
    r"\((?P<y>\d{4})\.(?P<m>\d{2})\)"
)


def parse_report_name(report_nm: str) -> PeriodicReport | None:
    """'[기재정정]사업보고서 (2023.12)' 같은 이름을 해석. 정기보고서가 아니면 None."""
    m = _REPORT_RE.match(report_nm.strip())
    if not m:
        return None
    return PeriodicReport(
        kind=m["kind"],
        period_year=int(m["y"]),
        period_month=int(m["m"]),
        is_correction="정정" in m["prefix"],
    )


def bsns_year_for(report: PeriodicReport, fiscal_end_month: int = 12) -> int:
    """재무제표 API 의 bsns_year.

    12월 결산은 보고 기간의 연도와 같다. 12월 결산이 아닌 회사의 기준은 실제 응답으로
    검증하기 전까지 추측하지 않는다 (틀린 연도의 재무 수치를 저장하지 않기 위해).
    """
    if fiscal_end_month != 12:
        raise NotImplementedError("12월 결산이 아닌 회사의 bsns_year 규칙은 아직 검증되지 않음")
    return report.period_year
