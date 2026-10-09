import { describe, expect, it } from "vitest";
import { passageOf } from "./passage";

const text = (parts: { text: string }[]) => parts.map((p) => p.text).join("");

describe("passageOf", () => {
  it("marks the cited chunk inside the wider context and the quoted numbers", () => {
    const body = "DS 부문 매출은 111조원이다.";
    const context = `반도체 사업을 한다.\n${body}\n메모리 수요가 늘었다.`;
    const start = context.indexOf(body);
    const q = context.indexOf("111조원");
    const p = passageOf({ body, context, highlight: [start, start + body.length], quoted: [[q, q + 5]] });
    expect(p.hasContext).toBe(true);
    expect(text(p.before)).toBe("반도체 사업을 한다.\n");
    expect(text(p.cited)).toBe(body);
    expect(text(p.after)).toBe("\n메모리 수요가 늘었다.");
    expect(p.cited.filter((x) => x.quote).map((x) => x.text)).toEqual(["111조원"]);
  });

  it("treats the whole body as cited when there is no context", () => {
    const p = passageOf({ body: "| DS | 111,066,000 |", context: null, highlight: null, quoted: [[7, 18]] });
    expect(p.hasContext).toBe(false);
    expect(p.before).toEqual([]);
    expect(p.cited).toEqual([
      { text: "| DS | ", quote: false },
      { text: "111,066,000", quote: true },
      { text: " |", quote: false },
    ]);
  });

  it("uses code point offsets like the backend", () => {
    // 𠀀 는 UTF-16 으로 두 칸이지만 파이썬 위치로는 한 칸
    const context = "𠀀 앞. 매출 5조원. 뒤";
    const p = passageOf({ body: "매출 5조원.", context, highlight: [5, 12], quoted: [[8, 11]] });
    expect(text(p.cited)).toBe("매출 5조원.");
    expect(p.cited.find((x) => x.quote)?.text).toBe("5조원");
  });

  it("ignores spans outside the text and overlapping spans", () => {
    const p = passageOf({ body: "abc", context: null, highlight: null, quoted: [[1, 2], [1, 3], [5, 9]] });
    expect(text(p.cited)).toBe("abc");
    expect(p.cited.map((x) => x.quote)).toEqual([false, true, true]);
  });
});
