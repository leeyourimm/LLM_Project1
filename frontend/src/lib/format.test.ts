import { describe, expect, it } from "vitest";
import { pct, signedPct, won, wonShort } from "./format";
import { parseSse } from "./sse";

describe("won", () => {
  it("formats like the backend", () => {
    expect(won(300_870_903_000_000)).toBe("300조 8,709억원");
    expect(won(-6_566_976_000_000)).toBe("-6조 5,670억원");
    expect(won(258_000_000_000_000)).toBe("258조원");
    expect(won(54_321_000_000)).toBe("543억원");
    expect(won(1234)).toBe("1,234원");
    expect(won(null)).toBe("-");
  });
  it("short axis labels", () => {
    expect(wonShort(1.25e12)).toBe("1.3조");
    expect(wonShort(3.4e11)).toBe("3,400억");
  });
  it("percent", () => {
    expect(pct(10.876)).toBe("10.9%");
    expect(signedPct(16.2)).toBe("+16.2%");
    expect(signedPct(-3)).toBe("-3.0%");
  });
});

describe("parseSse", () => {
  it("keeps partial blocks for later", () => {
    const { events, rest } = parseSse(
      'event: meta\ndata: {"conversation_id": 1}\n\nevent: token\ndata: {"text": "안"}\n\nevent: tok',
    );
    expect(events).toEqual([
      { event: "meta", data: { conversation_id: 1 } },
      { event: "token", data: { text: "안" } },
    ]);
    expect(rest).toBe("event: tok");
  });
});
