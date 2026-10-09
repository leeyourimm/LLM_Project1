import { describe, expect, it } from "vitest";
import { safeNext } from "./redirect";

describe("safeNext", () => {
  it("keeps paths on this site", () => {
    expect(safeNext("/company/005930?years=5")).toBe("/company/005930?years=5");
    expect(safeNext("/")).toBe("/");
  });
  it("rejects other sites", () => {
    const bad = [null, "", "https://evil.example", "//evil.example", "/\\evil.example", "/\t/evil.example", "javascript:alert(1)"];
    for (const b of bad) expect(safeNext(b)).toBe("/");
  });
});
