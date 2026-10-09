"""평가 문항 형식 (JSONL 한 줄 = 한 문항)."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ExpectedSource:
    """정답 근거 위치. rcept_no 나 섹션 경로 일부 중 아는 것만 적는다."""

    rcept_no: str | None = None
    section: str | None = None  # 섹션 경로에 포함돼야 하는 문자열 (예: "사업의 개요")
    finance: bool = False  # 재무 DB 조회 결과가 근거여야 하는 문항


@dataclass
class EvalCase:
    id: str
    question: str
    # numeric: 숫자 정답 / text: 서술형 / unanswerable: 공시에 없는 내용이라 "못 찾음" 이 정답
    category: str
    stocks: list[str] = field(default_factory=list)
    corp_codes: list[str] = field(default_factory=list)
    expected_numbers: list[str] = field(default_factory=list)  # "300조 8,709억원", "16.2%"
    expected_keywords: list[str] = field(default_factory=list)
    expected_sources: list[ExpectedSource] = field(default_factory=list)
    source: str = "manual"  # manual / generated / feedback
    note: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        d = dict(d)
        d["expected_sources"] = [ExpectedSource(**s) for s in d.get("expected_sources", [])]
        return cls(**d)

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, [], "")}


CATEGORIES = ("numeric", "text", "unanswerable")


def load_cases(*paths: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for path in paths:
        for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("//"):
                continue
            try:
                case = EvalCase.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"{path}:{lineno} 문항 형식 오류: {e}") from e
            if case.category not in CATEGORIES:
                raise ValueError(f"{path}:{lineno} 알 수 없는 category: {case.category}")
            if case.id in seen:
                raise ValueError(f"{path}:{lineno} 중복 id: {case.id}")
            seen.add(case.id)
            cases.append(case)
    return cases


def save_cases(path: Path, cases: list[EvalCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(c.to_dict(), ensure_ascii=False) + "\n" for c in cases),
        encoding="utf-8",
    )
