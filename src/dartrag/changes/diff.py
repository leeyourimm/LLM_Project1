"""두 보고서(예: 2023 사업보고서와 2024 사업보고서)를 섹션별로 비교한다.

- 섹션은 번호를 떼고 이름으로 짝짓는다 ("II. 사업의 내용" ↔ "2. 사업의 내용").
  해마다 목차 번호가 바뀌어도 같은 섹션으로 본다.
- 문단·표 행 단위로 비교한다. 청크 경계는 해마다 달라서 청크끼리 비교하지 않는다.
- 숫자만 바뀐 문장(매년 갱신되는 금액·날짜)은 "숫자만 변경"으로 따로 세어 잡음을 줄인다.
- 위험, 소송, 최대주주 같은 섹션의 변화는 중요도를 높여 먼저 보여준다.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_NUMBERING_RE = re.compile(
    r"^\s*(?:[IVXLC]+\.|\d+(?:-\d+)*\.|\(\d+\)|[가-하]\.|\d+\))\s*", re.IGNORECASE
)
_DIGITS_RE = re.compile(r"\d[\d,.]*")
_TABLE_SEP_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")

IMPORTANT_SECTIONS = {
    "위험": 3,
    "소송": 3,
    "우발부채": 3,
    "제재": 3,
    "감사": 2,
    "계속기업": 3,
    "최대주주": 2,
    "배당": 2,
    "주요계약": 2,
    "연구개발": 1,
    "임원": 1,
    "사업의 개요": 1,
    "주요 제품": 1,
}


def section_key(path: list[str]) -> str:
    return " > ".join(_NUMBERING_RE.sub("", p).strip() for p in path if p.strip())


def _norm(s: str) -> str:
    return " ".join(s.split())


def _mask(s: str) -> str:
    return _DIGITS_RE.sub("#", _norm(s))


def units(bodies: list[str]) -> list[str]:
    """섹션 본문들 → 비교 단위(문단, 표 행). 표 머리글 반복과 빈 줄은 뺀다."""
    out: list[str] = []
    seen: set[str] = set()
    for body in bodies:
        for line in body.split("\n"):
            line = _norm(line)
            if not line or _TABLE_SEP_RE.match(line.replace(" ", "")) or line in seen:
                continue
            seen.add(line)
            out.append(line)
    return out


@dataclass
class SectionDiff:
    key: str
    status: str  # added / removed / changed
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[tuple[str, str]] = field(default_factory=list)
    numbers_only: list[tuple[str, str]] = field(default_factory=list)

    @property
    def importance(self) -> int:
        return max((w for k, w in IMPORTANT_SECTIONS.items() if k in self.key), default=0)

    @property
    def size(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    @property
    def substantive(self) -> bool:
        return self.status != "changed" or self.size > 0


MAX_PAIRS = 40_000


def diff_units(old: list[str], new: list[str], key: str) -> SectionDiff:
    d = SectionDiff(key, "changed")
    sm = SequenceMatcher(None, [_mask(u) for u in old], [_mask(u) for u in new], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for a, b in zip(old[i1:i2], new[j1:j2], strict=True):
                if a != b:
                    d.numbers_only.append((a, b))
            continue
        olds, news = old[i1:i2], new[j1:j2]
        # 바뀐 구간 안에서 비슷한 문장끼리 짝지어 "수정"으로 본다.
        # 구간이 너무 크면(표 전체 교체 등) 짝짓기를 건너뛰어 비교 시간을 제한한다
        pairable = len(olds) * len(news) <= MAX_PAIRS
        used: set[int] = set()
        for a in olds:
            best, best_j = 0.0, None
            for j, b in enumerate(news if pairable else []):
                if j in used:
                    continue
                m = SequenceMatcher(None, _mask(a), _mask(b), autojunk=False)
                if (
                    m.real_quick_ratio() > best
                    and m.quick_ratio() > best
                    and (r := m.ratio()) > best
                ):
                    best, best_j = r, j
            if best_j is not None and best >= 0.6:
                used.add(best_j)
                d.modified.append((a, news[best_j]))
            else:
                d.removed.append(a)
        d.added += [b for j, b in enumerate(news) if j not in used]
    return d


def diff_reports(
    old_sections: dict[str, list[str]], new_sections: dict[str, list[str]]
) -> list[SectionDiff]:
    """섹션 키 → 본문 목록 두 개를 비교해, 중요하고 많이 바뀐 섹션부터 돌려준다."""
    out: list[SectionDiff] = []
    for key in list(dict.fromkeys([*new_sections, *old_sections])):
        old_u = units(old_sections.get(key, []))
        new_u = units(new_sections.get(key, []))
        if key not in old_sections:
            out.append(SectionDiff(key, "added", added=new_u))
        elif key not in new_sections:
            out.append(SectionDiff(key, "removed", removed=old_u))
        else:
            d = diff_units(old_u, new_u, key)
            if d.size or d.numbers_only:
                out.append(d)
    return sorted(out, key=lambda d: (-d.importance, -d.size))


STATUS_LABEL = {"added": "새 섹션", "removed": "삭제된 섹션", "changed": "변경"}


def render_markdown(
    diffs: list[SectionDiff], title: str, *, max_items: int = 8, max_chars: int = 300
) -> str:
    def clip(s: str) -> str:
        return s if len(s) <= max_chars else s[:max_chars] + "…"

    lines = [f"# {title}", ""]
    substantive = [d for d in diffs if d.substantive]
    minor = [d for d in diffs if not d.substantive]
    if not substantive:
        lines.append("내용이 바뀐 섹션이 없습니다 (숫자만 갱신된 섹션 제외).")
    for d in substantive:
        star = " ⚠️" if d.importance >= 2 else ""
        lines += [
            f"## {d.key}{star}",
            f"{STATUS_LABEL[d.status]} · 추가 {len(d.added)} · 삭제 {len(d.removed)}"
            f" · 수정 {len(d.modified)} · 숫자만 변경 {len(d.numbers_only)}",
            "",
        ]
        lines += [f"- ➕ {clip(u)}" for u in d.added[:max_items]]
        lines += [f"- ➖ {clip(u)}" for u in d.removed[:max_items]]
        for a, b in d.modified[:max_items]:
            lines += [f"- ✏️ 이전: {clip(a)}", f"  이후: {clip(b)}"]
        hidden = sum(max(0, n - max_items) for n in (len(d.added), len(d.removed), len(d.modified)))
        if hidden:
            lines.append(f"- … 외 {hidden}건")
        lines.append("")
    if minor:
        lines += ["## 숫자만 갱신된 섹션", ""]
        lines += [f"- {d.key} ({len(d.numbers_only)}건)" for d in minor]
    return "\n".join(lines).rstrip() + "\n"
