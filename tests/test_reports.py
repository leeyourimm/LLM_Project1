import pytest

from dartrag.dart.reports import ReportCode, bsns_year_for, parse_report_name


@pytest.mark.parametrize(
    ("name", "kind", "year", "month", "correction"),
    [
        ("사업보고서 (2023.12)", "사업보고서", 2023, 12, False),
        ("[기재정정]사업보고서 (2023.12)", "사업보고서", 2023, 12, True),
        ("[첨부정정] 반기보고서 (2024.06)", "반기보고서", 2024, 6, True),
        ("분기보고서 (2024.03)", "분기보고서", 2024, 3, False),
    ],
)
def test_parse_report_name(name, kind, year, month, correction):
    r = parse_report_name(name)
    assert (r.kind, r.period_year, r.period_month, r.is_correction) == (
        kind,
        year,
        month,
        correction,
    )


def test_non_periodic_report_is_none():
    assert parse_report_name("주요사항보고서(유상증자결정)") is None


@pytest.mark.parametrize(
    ("name", "fiscal_end", "code"),
    [
        ("사업보고서 (2023.12)", 12, ReportCode.ANNUAL),
        ("반기보고서 (2024.06)", 12, ReportCode.HALF),
        ("분기보고서 (2024.03)", 12, ReportCode.Q1),
        ("분기보고서 (2024.09)", 12, ReportCode.Q3),
        # 3월 결산: 6월이 1분기, 12월이 3분기
        ("분기보고서 (2024.06)", 3, ReportCode.Q1),
        ("분기보고서 (2024.12)", 3, ReportCode.Q3),
        ("사업보고서 (2024.03)", 3, ReportCode.ANNUAL),
    ],
)
def test_reprt_code(name, fiscal_end, code):
    assert parse_report_name(name).reprt_code(fiscal_end) == code


def test_bsns_year_refuses_unverified_fiscal_year():
    r = parse_report_name("사업보고서 (2024.03)")
    assert bsns_year_for(parse_report_name("사업보고서 (2023.12)")) == 2023
    with pytest.raises(NotImplementedError):
        bsns_year_for(r, fiscal_end_month=3)
