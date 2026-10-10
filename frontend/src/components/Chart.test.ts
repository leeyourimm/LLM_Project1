import { describe, expect, it } from "vitest";
import { labelIndexes } from "./Chart";

const sorted = (s: Set<number>) => [...s].sort((a, b) => a - b);

describe("labelIndexes", () => {
  it("마지막 점에서부터 같은 간격으로 고른다", () => {
    // 분기 12개: 23.3Q … 26.2Q. 끝의 26.1Q·26.2Q 가 붙어 나오지 않고 4분기 라벨도 빠지지 않는다
    expect(sorted(labelIndexes(12, 8))).toEqual([1, 3, 5, 7, 9, 11]);
    expect(sorted(labelIndexes(12, 4))).toEqual([2, 5, 8, 11]);
  });

  it("자리가 넉넉하면 모두, 없으면 마지막 하나만", () => {
    expect(sorted(labelIndexes(5, 8))).toEqual([0, 1, 2, 3, 4]);
    expect(sorted(labelIndexes(5, 0))).toEqual([4]);
    expect(sorted(labelIndexes(0, 8))).toEqual([]);
  });
});
