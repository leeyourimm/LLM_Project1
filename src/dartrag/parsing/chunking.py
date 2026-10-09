"""섹션 경계를 지키는 청킹.

- 문단은 섹션 안에서 max_chars 까지 묶고, 섹션을 넘어 섞지 않는다.
- 표는 별도 청크로 만들고, 길면 행 단위로 나누되 머리글 행을 반복한다.
- 각 청크 앞에 "회사 / 보고서 / 섹션 경로" 맥락 문장을 붙여 검색 정확도를 높인다
  (contextual retrieval). 원문 텍스트는 body 에 따로 보관한다.
"""

import hashlib
from dataclasses import dataclass

from dartrag.parsing.document import Paragraph, ParsedDocument, Section, Table


@dataclass
class Chunk:
    chunk_id: str
    kind: str  # text / table
    section_path: list[str]
    context: str
    body: str
    ord: int
    unit: str | None = None

    @property
    def text(self) -> str:
        """임베딩·검색에 쓰는 전체 텍스트."""
        return f"{self.context}\n\n{self.body}"


def chunk_document(
    doc: ParsedDocument,
    *,
    rcept_no: str,
    corp_name: str,
    report_label: str,
    max_chars: int = 1500,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in doc.sections:
        for kind, body, unit in _section_pieces(section, max_chars):
            context = _context(corp_name, report_label, section, unit)
            ord_ = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(rcept_no, doc.source_file, ord_, body),
                    kind=kind,
                    section_path=section.path,
                    context=context,
                    body=body,
                    ord=ord_,
                    unit=unit,
                )
            )
    return chunks


def _section_pieces(section: Section, max_chars: int):
    buf: list[str] = []
    size = 0
    for block in section.blocks:
        if isinstance(block, Paragraph):
            for piece in _split_long(block.text, max_chars):
                if buf and size + len(piece) > max_chars:
                    yield "text", "\n".join(buf), None
                    buf, size = [], 0
                buf.append(piece)
                size += len(piece) + 1
        elif isinstance(block, Table):
            if buf:
                yield "text", "\n".join(buf), None
                buf, size = [], 0
            for body in _split_table(block, max_chars):
                yield "table", body, block.unit
    if buf:
        yield "text", "\n".join(buf), None


def _split_long(text: str, max_chars: int) -> list[str]:
    """한 문단이 너무 길면 문장 경계(. 다.)에서 자른다."""
    if len(text) <= max_chars:
        return [text]
    pieces, current = [], ""
    for sentence in text.replace("다. ", "다.\n").replace(". ", ".\n").split("\n"):
        if current and len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
        while len(current) > max_chars:
            pieces.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        pieces.append(current)
    return pieces


def _split_table(table: Table, max_chars: int) -> list[str]:
    caption = table.caption or ""
    header, body_rows = table.rows[:1], table.rows[1:]
    pieces: list[str] = []
    rows: list[list[str]] = []
    for row in body_rows:
        candidate = Table(rows=header + rows + [row])
        if rows and len(candidate.to_markdown()) + len(caption) > max_chars:
            pieces.append(_with_caption(caption, Table(rows=header + rows)))
            rows = []
        rows.append(row)
    pieces.append(_with_caption(caption, Table(rows=header + rows)))
    return pieces


def _with_caption(caption: str, table: Table) -> str:
    md = table.to_markdown()
    return f"{caption}\n{md}" if caption else md


def _context(corp_name: str, report_label: str, section: Section, unit: str | None) -> str:
    parts = [corp_name, report_label]
    if section.path:
        parts.append(section.path_text)
    context = " / ".join(parts)
    if unit:
        context += f" (단위: {unit})"
    return context


def _chunk_id(rcept_no: str, source_file: str, ord_: int, body: str) -> str:
    digest = hashlib.sha1(f"{source_file}:{ord_}:{body}".encode()).hexdigest()[:12]
    return f"{rcept_no}-{digest}"
