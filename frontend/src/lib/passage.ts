// 출처 패널에 보일 원문을 "앞 문단 / 인용 문단 / 뒤 문단"으로 나누고, 그 안에서 답변에 옮긴 숫자를 표시한다.
//
// 백엔드가 주는 위치는 파이썬 문자열 기준(유니코드 코드 포인트)이다. 자바스크립트 문자열은 UTF-16 이라
// 한자 확장 영역이나 이모지처럼 두 칸을 쓰는 글자가 있으면 어긋나므로 코드 포인트 배열로 자른다.

import type { Source, Span } from "./types";

export interface Part {
  text: string;
  quote: boolean; // 답변에 옮긴 숫자
}

export interface Passage {
  before: Part[];
  cited: Part[]; // 답변이 인용한 문단 (검색된 청크)
  after: Part[];
  // 앞뒤 문단까지 보여 주는지 (아니면 본문 전체가 인용 문단)
  hasContext: boolean;
}

function clampSpan([a, b]: Span, length: number): Span | null {
  const start = Math.max(0, Math.min(a, length));
  const end = Math.max(0, Math.min(b, length));
  return end > start ? [start, end] : null;
}

/** chars[from, to) 를 숫자 위치(quotes) 기준으로 나눈다. */
function split(chars: string[], from: number, to: number, quotes: Span[]): Part[] {
  const parts: Part[] = [];
  let pos = from;
  for (const [qa, qb] of quotes) {
    const a = Math.max(qa, from, pos);
    const b = Math.min(qb, to);
    if (b <= a) continue;
    if (a > pos) parts.push({ text: chars.slice(pos, a).join(""), quote: false });
    parts.push({ text: chars.slice(a, b).join(""), quote: true });
    pos = b;
  }
  if (pos < to) parts.push({ text: chars.slice(pos, to).join(""), quote: false });
  return parts;
}

export function passageOf(source: Pick<Source, "body" | "context" | "highlight" | "quoted">): Passage {
  const useContext = Boolean(source.context && source.highlight);
  const chars = Array.from(useContext ? (source.context as string) : source.body);
  const cited = (useContext && clampSpan(source.highlight as Span, chars.length)) || [0, chars.length];
  const quotes = (source.quoted ?? [])
    .map((q) => clampSpan(q, chars.length))
    .filter((q): q is Span => q !== null)
    .sort((x, y) => x[0] - y[0]);
  return {
    before: split(chars, 0, cited[0], quotes),
    cited: split(chars, cited[0], cited[1], quotes),
    after: split(chars, cited[1], chars.length, quotes),
    hasContext: useContext && (cited[0] > 0 || cited[1] < chars.length),
  };
}
