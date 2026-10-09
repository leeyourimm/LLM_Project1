import { describe, expect, it } from "vitest";
import { linkToken } from "./token";

describe("linkToken", () => {
  it("서버가 만드는 모양의 토큰만 받는다", () => {
    const t = "Abc_def-0123456789abcdefghijklmnopqrstuvwxy";
    expect(linkToken(t)).toBe(t);
    expect(linkToken(` ${t} `)).toBe(t);
  });

  it("비었거나 잘렸거나 이상한 값은 버린다", () => {
    expect(linkToken(null)).toBeNull();
    expect(linkToken("")).toBeNull();
    expect(linkToken("short")).toBeNull();
    expect(linkToken("a".repeat(101))).toBeNull();
    expect(linkToken("abc/def<script>0123456789012345")).toBeNull();
    expect(linkToken("abcdefghij0123456789%20")).toBeNull();
  });
});
