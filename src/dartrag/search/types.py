from dataclasses import dataclass, field


@dataclass
class SearchFilter:
    corp_codes: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    report_kinds: list[str] = field(default_factory=list)


@dataclass
class IndexedChunk:
    """검색 인덱스에 넣는 청크와 필터용 메타데이터."""

    chunk_id: str
    rcept_no: str
    corp_code: str
    corp_name: str
    report_kind: str | None
    period_key: str | None
    kind: str
    section_path: list[str]
    text: str  # 맥락 문장 + 본문

    @property
    def period_year(self) -> int | None:
        return int(self.period_key[:4]) if self.period_key else None

    def payload(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "rcept_no": self.rcept_no,
            "corp_code": self.corp_code,
            "corp_name": self.corp_name,
            "report_kind": self.report_kind,
            "period_key": self.period_key,
            "period_year": self.period_year,
            "kind": self.kind,
            "section_path": self.section_path,
        }
