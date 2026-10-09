import { describe, expect, it } from "vitest";
import { signupHref, timeLeft } from "./guest";

describe("timeLeft", () => {
  const now = new Date("2026-10-09T00:00:00Z");
  it("shows hours and minutes", () => {
    expect(timeLeft("2026-10-09T23:10:00Z", now)).toBe("23시간 10분");
    expect(timeLeft("2026-10-10T00:00:00+00:00", now)).toBe("24시간");
    expect(timeLeft("2026-10-09T00:45:30Z", now)).toBe("45분");
    expect(timeLeft("2026-10-09T00:00:20Z", now)).toBe("1분 미만");
  });
  it("handles past or missing times", () => {
    expect(timeLeft("2026-10-08T23:00:00Z", now)).toBe("곧");
    expect(timeLeft(null, now)).toBe("곧");
    expect(timeLeft("not a date", now)).toBe("곧");
  });
});

describe("signupHref", () => {
  it("comes back to the page the guest was on", () => {
    expect(signupHref("/company/005930")).toBe("/signup?next=%2Fcompany%2F005930");
    expect(signupHref("/")).toBe("/signup");
    expect(signupHref("/login")).toBe("/signup");
    expect(signupHref(null)).toBe("/signup");
  });
});
