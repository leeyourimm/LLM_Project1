import pytest

from dartrag.changes import diff as diff_mod
from dartrag.changes import diff_reports, render_markdown, section_key
from dartrag.changes.compare import compare_filings, latest_pair, sections_of
from dartrag.changes.diff import diff_units, units


def test_section_key_ignores_numbering():
    assert section_key(["II. 사업의 내용", "1. 사업의 개요"]) == "사업의 내용 > 사업의 개요"
    assert section_key(["2. 사업의 내용", "(1) 사업의 개요"]) == "사업의 내용 > 사업의 개요"
    assert section_key(["XI. 그 밖에", "가. 우발부채"]) == "그 밖에 > 우발부채"


def test_units_drop_separators_and_repeated_headers():
    bodies = [
        "| 부문 | 매출 |\n| --- | --- |\n| DS | 1 |",
        "| 부문 | 매출 |\n| --- | --- |\n| DX | 2 |",
    ]
    assert units(bodies) == ["| 부문 | 매출 |", "| DS | 1 |", "| DX | 2 |"]


def test_numbers_only_changes_are_separated():
    d = diff_units(
        ["2023년 매출은 258조원이다.", "본사는 수원이다."],
        ["2024년 매출은 300조원이다.", "본사는 수원이다."],
        "k",
    )
    assert d.size == 0 and len(d.numbers_only) == 1 and not d.substantive


def test_added_removed_and_modified():
    d = diff_units(
        ["당사는 DX, DS 부문으로 구성된다.", "PC 사업을 철수했다."],
        ["당사는 DX, DS, Harman 부문으로 구성된다.", "HBM 신규 투자를 시작했다."],
        "k",
    )
    assert d.modified == [
        ("당사는 DX, DS 부문으로 구성된다.", "당사는 DX, DS, Harman 부문으로 구성된다.")
    ]
    assert d.removed == ["PC 사업을 철수했다."]
    assert d.added == ["HBM 신규 투자를 시작했다."]


def test_large_blocks_skip_pairing(monkeypatch):
    monkeypatch.setattr(diff_mod, "MAX_PAIRS", 0)
    d = diff_units(["가나다라 마바사"], ["가나다라 마바사아"], "k")
    assert d.modified == [] and d.removed and d.added


OLD = {
    "사업의 내용 > 사업의 개요": ["당사는 DX, DS 부문으로 구성된다.\n2023년 매출은 258조원이다."],
    "위험관리": ["환율 위험이 있다."],
    "옛 섹션": ["없어진 내용"],
    "회사의 개요": ["2023년 12월 기준 임직원 수 120,000명"],
}
NEW = {
    "사업의 내용 > 사업의 개요": [
        "당사는 DX, DS 부문으로 구성된다.\n2024년 매출은 300조원이다.\nHBM 투자."
    ],
    "위험관리": ["환율 위험이 있다.\n중국 수출 규제 위험이 생겼다."],
    "새 섹션": ["새 내용"],
    "회사의 개요": ["2024년 12월 기준 임직원 수 125,000명"],
}


def test_diff_reports_orders_important_sections_first():
    diffs = diff_reports(OLD, NEW)
    assert diffs[0].key == "위험관리" and diffs[0].importance == 3
    by_key = {d.key: d for d in diffs}
    assert by_key["새 섹션"].status == "added" and by_key["옛 섹션"].status == "removed"
    assert not by_key["회사의 개요"].substantive


def test_render_markdown():
    md = render_markdown(diff_reports(OLD, NEW), "삼성전자 변경점", max_items=1)
    assert md.startswith("# 삼성전자 변경점\n")
    assert "## 위험관리 ⚠️" in md and "- ➕ 중국 수출 규제 위험이 생겼다." in md
    assert "## 숫자만 갱신된 섹션\n\n- 회사의 개요 (1건)" in md
    assert render_markdown([], "t").count("내용이 바뀐 섹션이 없습니다") == 1


def test_sections_of_uses_main_document_only():
    rows = [
        ("20250311000001_00760.xml", 0, ["감사보고서"], "첨부"),
        ("20250311000001.xml", 1, ["II. 사업의 내용"], "둘째"),
        ("20250311000001.xml", 0, ["II. 사업의 내용"], "첫째"),
    ]
    assert sections_of(rows) == {"사업의 내용": ["첫째", "둘째"]}


class FakeRepo:
    def __init__(self, parsed=True):
        self.parsed = parsed

    def filing_info(self, rcept_no):
        if rcept_no == "missing":
            return None
        return {
            "rcept_no": rcept_no,
            "report_nm": f"사업보고서 ({'2023' if rcept_no.endswith('1') else '2024'}.12)",
            "corp_name": "삼성전자",
            "parsed": self.parsed,
        }

    def chunk_rows(self, rcept_no):
        body = "환율 위험" if rcept_no.endswith("1") else "환율 위험\n관세 위험"
        return [(f"{rcept_no}.xml", 0, ["III. 위험관리"], body)]

    def latest_filing_per_period(self, corp_code, kind):
        return ["20240311000001", "20250311000002"]


def test_compare_filings_with_repo():
    result = compare_filings(FakeRepo(), "20240311000001", "20250311000002")
    md = result.markdown()
    assert md.startswith("# 삼성전자 변경점: 사업보고서 (2023.12) → 사업보고서 (2024.12)\n")
    assert "rcpNo=20250311000002" in md and "- ➕ 관세 위험" in md
    assert latest_pair(FakeRepo(), "00126380") == ("20240311000001", "20250311000002")
    with pytest.raises(ValueError, match="DB 에 없습니다"):
        compare_filings(FakeRepo(), "missing", "20250311000002")
    with pytest.raises(ValueError, match="파싱하지 않았습니다"):
        compare_filings(FakeRepo(parsed=False), "20240311000001", "20250311000002")
