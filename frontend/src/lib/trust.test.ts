import { describe, expect, it } from "vitest";
import { ageLabel, trustView } from "./trust";
import type { Source, Trust } from "./types";

const LEGACY = { sources: [] as Source[], warnings: [] as string[], unverified: [] as string[] };

function trust(patch: Partial<Trust> = {}): Trust {
  return {
    sources: 2,
    filings: 1,
    numbers_checked: 3,
    unverified_numbers: [],
    invalid_citations: [],
    uncited: false,
    newest: {
      rcept_no: "20250311000001",
      report_nm: "사업보고서 (2024.12)",
      rcept_dt: "2025-03-11",
      corp_name: "삼성전자",
      age_days: 212,
    },
    companies: [
      {
        corp_code: "00126380",
        corp_name: "삼성전자",
        cited: { rcept_no: "20250311000001", report_nm: "사업보고서 (2024.12)", rcept_dt: "2025-03-11" },
        latest: { rcept_no: "20250311000001", report_nm: "사업보고서 (2024.12)", rcept_dt: "2025-03-11", indexed: true },
        is_latest: true,
      },
    ],
    checked_on: "2025-10-09",
    ...patch,
  };
}

const chips = (t: Trust) => trustView(t, LEGACY)?.chips.map((c) => [c.tone, c.text]);

describe("ageLabel", () => {
  it("reads like a person would say it", () => {
    expect(ageLabel(0)).toBe("오늘");
    expect(ageLabel(-3)).toBe("오늘");
    expect(ageLabel(1)).toBe("1일 전");
    expect(ageLabel(13)).toBe("13일 전");
    expect(ageLabel(14)).toBe("2주 전");
    expect(ageLabel(59)).toBe("8주 전");
    expect(ageLabel(60)).toBe("2개월 전");
    expect(ageLabel(212)).toBe("7개월 전");
    expect(ageLabel(365)).toBe("1년 전");
    expect(ageLabel(365 + 70)).toBe("1년 2개월 전");
  });
});

describe("trustView", () => {
  it("states the facts when everything checks out", () => {
    expect(chips(trust())).toEqual([
      ["good", "출처 2개 · 공시 1건"],
      ["good", "최신 공시 기준 · 7개월 전 접수"],
      ["good", "숫자 3개 원문과 일치"],
    ]);
    const view = trustView(trust(), LEGACY)!;
    expect(view.hasWarning).toBe(false);
    expect(view.expand).toBe(false);
    expect(view.checkedOn).toBe("2025-10-09");
    expect(view.details.map((d) => d.text)).toContain(
      "삼성전자: 인용한 가장 최근 공시 「사업보고서 (2024.12)」(2025-03-11 접수)가 이 회사의 최신 정기공시입니다.",
    );
    // 퍼센트 점수 같은 것은 만들지 않는다
    expect(JSON.stringify(view)).not.toMatch(/\d+%/);
  });

  it("uses the singular for one source", () => {
    const texts = (t: Trust) => trustView(t, LEGACY)!.details.map((d) => d.text);
    expect(texts(trust())).toContain("답변이 출처 2개를 인용했고, 이 출처들은 공시 1건에서 나왔습니다.");
    expect(texts(trust({ sources: 1 }))).toContain("답변이 출처 1개를 인용했고, 이 출처는 공시 1건에서 나왔습니다.");
  });

  it("warns when a newer filing exists and says if it is still being indexed", () => {
    const t = trust({
      companies: [
        {
          ...trust().companies[0],
          latest: { rcept_no: "20250814000002", report_nm: "반기보고서 (2025.06)", rcept_dt: "2025-08-14", indexed: false },
          is_latest: false,
        },
      ],
    });
    expect(chips(t)?.[1]).toEqual(["warn", "더 최근 공시 있음 · 인용 공시는 7개월 전 접수"]);
    const detail = trustView(t, LEGACY)!.details.find((d) => d.key === "fresh-00126380")!;
    expect(detail.tone).toBe("warn");
    expect(detail.text).toContain("그 뒤에 「반기보고서 (2025.06)」(2025-08-14 접수)가 나왔습니다");
    expect(detail.text).toContain("아직 검색에 반영하는 중");
    expect(trustView(t, LEGACY)!.hasWarning).toBe(true);
    // 지난 기간을 물은 답변에서 흔한 일이라 자세한 설명을 펼치지는 않는다
    expect(trustView(t, LEGACY)!.expand).toBe(false);
  });

  it("does not claim freshness it could not check", () => {
    const t = trust({ companies: [{ ...trust().companies[0], latest: null, is_latest: null }] });
    expect(chips(t)?.[1]).toEqual(["info", "인용 공시 7개월 전 접수"]);
    // DB 에 없는 공시만 인용(재무 데이터 출처): 회사별 비교 없이 날짜만
    const onlyDate = trustView(trust({ companies: [] }), LEGACY)!;
    expect(onlyDate.details.find((d) => d.key === "fresh")?.text).toBe(
      "인용한 가장 최근 공시는 2025-03-11에 접수됐습니다 (7개월 전).",
    );
  });

  it("surfaces verifier warnings", () => {
    const t = trust({ unverified_numbers: ["11.1조원", "33조원"], invalid_citations: [9] });
    expect(chips(t)).toEqual([
      ["good", "출처 2개 · 공시 1건"],
      ["good", "최신 공시 기준 · 7개월 전 접수"],
      ["warn", "확인 안 된 숫자 2개"],
      ["warn", "없는 출처 번호 [9]"],
    ]);
    const texts = trustView(t, LEGACY)!.details.map((d) => d.text);
    expect(texts).toContain(
      "인용한 원문에서 찾지 못한 숫자 2개: 11.1조원, 33조원. 계산한 값이거나 옮겨 적다 틀렸을 수 있으니 원문과 비교해 보세요.",
    );
    expect(texts).toContain("출처 목록에 없는 번호 [9]를 인용했습니다. 그 문장은 근거가 확인되지 않았습니다.");
    expect(trustView(t, LEGACY)!.expand).toBe(true);
  });

  it("flags answers without citations and answers without numbers", () => {
    const t = trust({ uncited: true, sources: 0, filings: 0, newest: null, companies: [], numbers_checked: 0 });
    expect(chips(t)).toEqual([["warn", "출처 표시 없음"]]);
    expect(trustView(t, LEGACY)!.details.map((d) => d.text)).toEqual([
      "답변에 출처 번호가 없습니다. 근거 원문의 출처를 직접 확인하세요.",
      "원문과 대조할 숫자(금액·비율)가 없는 답변입니다.",
    ]);
  });

  it("hides for refusals and falls back for answers saved before this existed", () => {
    expect(trustView(null, LEGACY)).toBeNull();
    const old = trustView(undefined, {
      sources: [{ cited: true }, { cited: false }] as Source[],
      warnings: ["인용한 원문에서 확인되지 않은 숫자(오기이거나 계산값): 33조원"],
      unverified: ["33조원"],
    })!;
    expect(old.chips.map((c) => c.text)).toEqual(["출처 1개", "확인 안 된 숫자 1개"]);
    expect(old.details[0].text).toContain("33조원");
    expect(old.checkedOn).toBeNull();
  });
});
