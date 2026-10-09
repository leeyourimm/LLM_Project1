"""DART 공시 원문(XML) 파서.

원문 zip 에는 본문 XML 과 첨부(감사보고서 등) XML 이 들어 있다. 본문은 대략 이런 구조다.

    <DOCUMENT>
      <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
      <BODY>
        <SECTION-1><TITLE>II. 사업의 내용</TITLE>
          <SECTION-2><TITLE>1. 사업의 개요</TITLE>
            <P>...</P>
            <TABLE><TBODY><TR><TH>..</TH><TD>..</TD><TE>..</TE></TR></TBODY></TABLE>

공시마다 형식이 조금씩 달라서 잘못된 XML 도 최대한 복구해서 읽고, 모르는 태그는
본문 텍스트로 취급한다.
"""

import io
import re
import zipfile
from dataclasses import dataclass, field

from lxml import etree

CELL_TAGS = {"TD", "TH", "TE", "TU"}
SKIP_TAGS = {"TITLE", "COVER", "COVER-TITLE", "PGBRK", "IMAGE", "IMG"}

_UNIT_RE = re.compile(r"\(\s*단\s*위\s*[:：]\s*([^)]+?)\s*\)")
_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")


@dataclass
class Paragraph:
    text: str


@dataclass
class Table:
    rows: list[list[str]]
    caption: str | None = None
    unit: str | None = None

    def to_markdown(self) -> str:
        if not self.rows:
            return ""
        width = max(len(r) for r in self.rows)
        rows = [r + [""] * (width - len(r)) for r in self.rows]
        lines = ["| " + " | ".join(_md_cell(c) for c in rows[0]) + " |"]
        lines.append("|" + "---|" * width)
        lines += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows[1:]]
        return "\n".join(lines)


Block = Paragraph | Table


@dataclass
class Section:
    path: list[str]
    blocks: list[Block] = field(default_factory=list)

    @property
    def path_text(self) -> str:
        return " > ".join(self.path)


@dataclass
class ParsedDocument:
    name: str | None
    company: str | None
    sections: list[Section]
    source_file: str


def parse_document_zip(data: bytes) -> list[ParsedDocument]:
    """원문 zip 의 XML 파일을 모두 파싱한다. 첫 번째가 본문, 나머지는 첨부."""
    docs = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith(".xml"))
        # 본문 파일명은 접수번호.xml, 첨부는 접수번호_00760.xml 형태
        names.sort(key=lambda n: ("_" in n.rsplit("/", 1)[-1], n))
        for name in names:
            docs.append(parse_document_xml(zf.read(name), source_file=name))
    return docs


def parse_document_xml(xml: bytes, source_file: str = "") -> ParsedDocument:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(xml, parser=parser)
    if root is None:
        raise ValueError(f"XML 을 읽을 수 없습니다: {source_file}")

    name = _text(root.find(".//DOCUMENT-NAME"))
    company = _text(root.find(".//COMPANY-NAME"))
    body = root.find(".//BODY")
    sections: list[Section] = []
    _walk(body if body is not None else root, [], sections)
    return ParsedDocument(
        name=name or None,
        company=company or None,
        sections=[s for s in sections if s.blocks],
        source_file=source_file,
    )


def _walk(node, path: list[str], out: list[Section]) -> None:
    current = Section(path=list(path))
    out.append(current)
    pending_text: list[str] = []  # 표 바로 앞 문단 (캡션·단위 후보)

    for child in node:
        tag = _tag(child)
        if tag is None or tag in SKIP_TAGS:
            continue
        if tag.startswith("SECTION"):
            title = _text(child.find("TITLE")) or "(제목 없음)"
            _walk(child, path + [title], out)
            # 하위 섹션 뒤에 이어지는 내용은 새 블록 묶음으로
            current = Section(path=list(path))
            out.append(current)
            pending_text = []
        elif tag == "TABLE":
            table = _parse_table(child)
            if table.rows:
                _attach_caption(table, pending_text)
                current.blocks.append(table)
            pending_text = []
        elif tag == "TABLE-GROUP":
            for t in child.iter("TABLE"):
                table = _parse_table(t)
                if table.rows:
                    _attach_caption(table, pending_text)
                    current.blocks.append(table)
                pending_text = []
        else:
            text = _text(child)
            if text:
                current.blocks.append(Paragraph(text))
                pending_text = [text]


def _attach_caption(table: Table, pending: list[str]) -> None:
    if not pending:
        return
    text = pending[-1]
    if table.unit is None:
        m = _UNIT_RE.search(text)
        if m:
            table.unit = m.group(1)
    if len(text) <= 80:
        table.caption = text


def _parse_table(node) -> Table:
    """셀 병합(ROWSPAN/COLSPAN)을 풀어서 직사각형 표로 만든다."""
    grid: dict[tuple[int, int], str] = {}
    row_idx = 0
    for tr in node.iter("TR"):
        col = 0
        for cell in tr:
            tag = _tag(cell)
            if tag not in CELL_TAGS:
                continue
            while (row_idx, col) in grid:
                col += 1
            text = _text(cell)
            rowspan = _int_attr(cell, "ROWSPAN")
            colspan = _int_attr(cell, "COLSPAN")
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(row_idx + dr, col + dc)] = text
            col += colspan
        row_idx += 1

    if not grid:
        return Table(rows=[])
    n_rows = max(r for r, _ in grid) + 1
    n_cols = max(c for _, c in grid) + 1
    rows = [[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    rows = [r for r in rows if any(cell for cell in r)]

    unit = None
    for r in rows[:2]:
        for cell in r:
            m = _UNIT_RE.search(cell)
            if m:
                unit = m.group(1)
    return Table(rows=rows, unit=unit)


def _tag(node) -> str | None:
    if not isinstance(node.tag, str):  # 주석, 처리 지시문
        return None
    return node.tag.upper()


def _text(node) -> str:
    if node is None:
        return ""
    return _WS_RE.sub(" ", " ".join("".join(node.itertext()).split())).strip()


def _int_attr(node, name: str) -> int:
    value = node.get(name) or node.get(name.lower()) or "1"
    try:
        return max(1, min(int(value), 100))
    except ValueError:
        return 1


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
