from pathlib import Path

from dartrag.parsing import Paragraph, Table, chunk_document, parse_document_zip
from dartrag.parsing.document import parse_document_xml

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_report.xml").read_bytes()


def parse():
    return parse_document_xml(SAMPLE, source_file="20250311000001.xml")


def test_metadata_and_section_paths():
    doc = parse()
    assert doc.name == "사업보고서"
    assert doc.company == "삼성전자주식회사"
    assert [s.path for s in doc.sections] == [
        ["I. 회사의 개요", "1. 회사의 개요"],
        ["II. 사업의 내용", "1. 사업의 개요"],
    ]
    # 표지(COVER)는 본문에서 제외
    assert all("사 업 보 고 서" not in str(s.blocks) for s in doc.sections)


def test_recovers_from_unescaped_ampersand():
    texts = [b.text for b in parse().sections[1].blocks if isinstance(b, Paragraph)]
    assert any("DS 부문으로 구성" in t for t in texts)


def test_table_spans_unit_and_caption():
    table = next(b for b in parse().sections[1].blocks if isinstance(b, Table))
    assert table.rows == [
        ["부문", "매출액", "매출액"],
        ["부문", "2024년", "2023년"],
        ["DX", "1,747,887", "1,698,992"],
        ["DS", "1,110,660", "665,945"],
    ]
    assert table.unit == "억원"
    assert table.caption == "(단위 : 억원)"


def test_euc_kr_document():
    xml = SAMPLE.replace(b'encoding="utf-8"', b'encoding="euc-kr"').decode().encode("euc-kr")
    assert parse_document_xml(xml).company == "삼성전자주식회사"


def test_zip_puts_main_document_first():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        audit = SAMPLE.replace("사업보고서<".encode(), "감사보고서<".encode())
        zf.writestr("20250311000001_00760.xml", audit)
        zf.writestr("20250311000001.xml", SAMPLE)
    docs = parse_document_zip(buf.getvalue())
    assert [d.source_file for d in docs] == ["20250311000001.xml", "20250311000001_00760.xml"]
    assert [d.name for d in docs] == ["사업보고서", "감사보고서"]


def test_chunks_keep_sections_apart_and_add_context():
    chunks = chunk_document(
        parse(), rcept_no="20250311000001", corp_name="삼성전자", report_label="2024.12 사업보고서"
    )
    assert [c.kind for c in chunks] == ["text", "text", "table", "text"]
    assert chunks[0].context == "삼성전자 / 2024.12 사업보고서 / I. 회사의 개요 > 1. 회사의 개요"
    assert "설립" in chunks[0].body and "사업의 개요" not in chunks[0].body
    table = chunks[2]
    assert table.unit == "억원"
    assert table.context.endswith("(단위: 억원)")
    assert "| DX | 1,747,887 | 1,698,992 |" in table.body
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_long_table_is_split_with_header_repeated():
    rows = [["연도", "매출"]] + [[str(2000 + i), f"{i},000"] for i in range(200)]
    from dartrag.parsing.chunking import _split_table

    pieces = _split_table(Table(rows=rows, caption="매출 추이"), max_chars=300)
    assert len(pieces) > 1
    assert all(p.startswith("매출 추이\n| 연도 | 매출 |") for p in pieces)
    body_rows = sum(p.count("\n| 2") for p in pieces)
    assert body_rows == 200


def test_long_paragraph_is_split():
    from dartrag.parsing.chunking import _split_long

    text = "가나다라마바사입니다. " * 300
    pieces = _split_long(text, 500)
    assert all(len(p) <= 500 for p in pieces)
    assert "".join(pieces).replace(" ", "") == text.replace(" ", "")


def test_library_wrapper_keeps_sub_sections_and_tables():
    """실제 사업보고서는 "II. 사업의 내용" 의 하위 절과 재무제표 주석을 <LIBRARY> 로 감싼다."""
    xml = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT><DOCUMENT-NAME>사업보고서</DOCUMENT-NAME><BODY>
<SECTION-1><TITLE>II. 사업의 내용</TITLE>
<LIBRARY>
<SECTION-2><TITLE>1. 사업의 개요</TITLE><P>당사는 전자 회사입니다.</P></SECTION-2>
<SECTION-2><TITLE>2. 주요 제품 및 서비스</TITLE>
<TABLE><TBODY><TR><TH>부문</TH><TH>제품</TH></TR><TR><TD>DX</TD><TD>TV</TD></TR></TBODY></TABLE>
</SECTION-2>
</LIBRARY>
</SECTION-1>
</BODY></DOCUMENT>""".encode()
    doc = parse_document_xml(xml)
    paths = {tuple(s.path): s for s in doc.sections}
    assert ("II. 사업의 내용", "1. 사업의 개요") in paths
    products = paths[("II. 사업의 내용", "2. 주요 제품 및 서비스")]
    assert products.blocks[0].rows[1] == ["DX", "TV"]
    # 하위 절 내용이 상위 절에 한 문단으로 뭉치지 않는다
    assert ("II. 사업의 내용",) not in paths


def test_attached_statements_are_dropped_when_the_body_has_them():
    """사업보고서 본문 III 장과 첨부 감사보고서의 재무제표·주석은 같은 내용이다."""
    from dartrag.parsing.document import Paragraph, ParsedDocument, Section
    from dartrag.pipeline.parse import _has_statements, _without_statements

    def section(*path):
        return Section(path=list(path), blocks=[Paragraph("내용")])

    body = ParsedDocument(
        "사업보고서",
        "삼성전자",
        [section("III. 재무에 관한 사항", "3. 연결재무제표 주석")],
        "a.xml",
    )
    audit = ParsedDocument(
        "연결감사보고서",
        "삼성전자",
        [section("독립된 감사인의 감사보고서"), section("(첨부)연 결 재 무 제 표", "주석")],
        "b.xml",
    )
    assert _has_statements(body) and not _has_statements(audit)
    assert [s.path for s in _without_statements(audit).sections] == [["독립된 감사인의 감사보고서"]]
