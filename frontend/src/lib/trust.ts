// 답변 신뢰도 표시 문구. 점수(%)를 만들지 않고 백엔드가 확인한 사실만 쉬운 말로 옮긴다.
// 사실 자체는 src/dartrag/answer/trust.py 가 만든다.

import type { Source, Trust } from "./types";

export type Tone = "good" | "warn" | "info";

export interface TrustChip {
  key: string;
  tone: Tone;
  text: string;
}

export interface TrustView {
  chips: TrustChip[];
  details: { key: string; tone: Tone; text: string }[];
  hasWarning: boolean;
  // 답변 내용의 근거가 흔들리는 경고(확인 안 된 숫자, 없는 출처 번호, 출처 없음)가 있어 자세한 설명을 처음부터 펼친다.
  // 더 최근 공시가 있다는 것만으로는 펼치지 않는다 (지난 기간을 물은 답변에서는 흔한 일이다).
  expand: boolean;
  checkedOn: string | null;
}

/** 경과 일수 → "오늘", "3일 전", "2주 전", "7개월 전", "1년 2개월 전" */
export function ageLabel(days: number): string {
  const d = Math.max(0, Math.floor(days));
  if (d === 0) return "오늘";
  if (d < 14) return `${d}일 전`;
  if (d < 60) return `${Math.floor(d / 7)}주 전`;
  if (d < 365) return `${Math.floor(d / 30)}개월 전`;
  const years = Math.floor(d / 365);
  const months = Math.floor((d % 365) / 30);
  return months ? `${years}년 ${months}개월 전` : `${years}년 전`;
}

function filing(name: string | null, date: string | null): string {
  const title = name ? `「${name}」` : "공시";
  return date ? `${title}(${date} 접수)` : title;
}

function freshnessChip(t: Trust): TrustChip | null {
  if (!t.newest) return null;
  const age = `${ageLabel(t.newest.age_days)} 접수`;
  if (t.companies.some((c) => c.is_latest === false)) {
    return { key: "fresh", tone: "warn", text: `더 최근 공시 있음 · 인용 공시는 ${age}` };
  }
  if (t.companies.length && t.companies.every((c) => c.is_latest === true)) {
    return { key: "fresh", tone: "good", text: `최신 공시 기준 · ${age}` };
  }
  return { key: "fresh", tone: "info", text: `인용 공시 ${age}` };
}

function fromTrust(t: Trust): TrustView {
  const chips: TrustChip[] = [];
  const details: TrustView["details"] = [];

  if (t.uncited) {
    chips.push({ key: "sources", tone: "warn", text: "출처 표시 없음" });
    details.push({ key: "sources", tone: "warn", text: "답변에 출처 번호가 없습니다. 근거 원문의 출처를 직접 확인하세요." });
  } else {
    const filings = t.filings ? ` · 공시 ${t.filings}건` : "";
    chips.push({ key: "sources", tone: "good", text: `출처 ${t.sources}개${filings}` });
    details.push({
      key: "sources",
      tone: "good",
      text: t.filings
        ? `답변이 출처 ${t.sources}개를 인용했고, ${t.sources > 1 ? "이 출처들은" : "이 출처는"} 공시 ${t.filings}건에서 나왔습니다.`
        : `답변이 출처 ${t.sources}개를 인용했습니다.`,
    });
  }

  const fresh = freshnessChip(t);
  if (fresh) chips.push(fresh);
  for (const c of t.companies) {
    const cited = filing(c.cited.report_nm, c.cited.rcept_dt);
    if (c.is_latest === true) {
      details.push({
        key: `fresh-${c.corp_code}`,
        tone: "good",
        text: `${c.corp_name}: 인용한 가장 최근 공시 ${cited}가 이 회사의 최신 정기공시입니다.`,
      });
    } else if (c.is_latest === false && c.latest) {
      const pending = c.latest.indexed ? "" : " 새 공시는 아직 검색에 반영하는 중입니다.";
      details.push({
        key: `fresh-${c.corp_code}`,
        tone: "warn",
        text:
          `${c.corp_name}: 인용한 가장 최근 공시는 ${cited}이고, 그 뒤에 ` +
          `${filing(c.latest.report_nm, c.latest.rcept_dt)}가 나왔습니다. 지난 기간을 물었다면 문제없습니다.${pending}`,
      });
    } else {
      details.push({
        key: `fresh-${c.corp_code}`,
        tone: "info",
        text: `${c.corp_name}: 인용한 공시는 ${cited}입니다. 최신 공시인지는 확인하지 못했습니다.`,
      });
    }
  }
  if (t.newest && !t.companies.length) {
    details.push({
      key: "fresh",
      tone: "info",
      text: `인용한 가장 최근 공시는 ${t.newest.rcept_dt}에 접수됐습니다 (${ageLabel(t.newest.age_days)}).`,
    });
  }

  const unverified = t.unverified_numbers;
  if (unverified.length) {
    chips.push({ key: "numbers", tone: "warn", text: `확인 안 된 숫자 ${unverified.length}개` });
    details.push({
      key: "numbers",
      tone: "warn",
      text:
        `인용한 원문에서 찾지 못한 숫자 ${unverified.length}개: ${unverified.join(", ")}. ` +
        "계산한 값이거나 옮겨 적다 틀렸을 수 있으니 원문과 비교해 보세요.",
    });
  } else if (t.numbers_checked) {
    chips.push({ key: "numbers", tone: "good", text: `숫자 ${t.numbers_checked}개 원문과 일치` });
    details.push({
      key: "numbers",
      tone: "good",
      text: `답변의 숫자 ${t.numbers_checked}개를 인용한 원문과 대조했고 모두 원문에 있습니다.`,
    });
  } else {
    details.push({ key: "numbers", tone: "info", text: "원문과 대조할 숫자(금액·비율)가 없는 답변입니다." });
  }

  if (t.invalid_citations.length) {
    const nums = t.invalid_citations.map((n) => `[${n}]`).join(", ");
    chips.push({ key: "citations", tone: "warn", text: `없는 출처 번호 ${nums}` });
    details.push({
      key: "citations",
      tone: "warn",
      text: `출처 목록에 없는 번호 ${nums}를 인용했습니다. 그 문장은 근거가 확인되지 않았습니다.`,
    });
  }

  return {
    chips,
    details,
    hasWarning: chips.some((c) => c.tone === "warn"),
    expand: chips.some((c) => c.tone === "warn" && c.key !== "fresh"),
    checkedOn: t.checked_on,
  };
}

/** 이 기능 전에 저장한 답변: 출처 수와 검증 경고 문장만 보여 준다. */
function fromLegacy(sources: Source[], warnings: string[], unverified: string[]): TrustView {
  const cited = sources.filter((s) => s.cited).length;
  const chips: TrustChip[] = cited
    ? [{ key: "sources", tone: "good", text: `출처 ${cited}개` }]
    : [{ key: "sources", tone: "warn", text: "출처 표시 없음" }];
  if (unverified.length) chips.push({ key: "numbers", tone: "warn", text: `확인 안 된 숫자 ${unverified.length}개` });
  const details = warnings.map((w, i) => ({ key: `w${i}`, tone: "warn" as Tone, text: w }));
  const hasWarning = chips.some((c) => c.tone === "warn") || details.length > 0;
  return { chips, details, hasWarning, expand: hasWarning, checkedOn: null };
}

/**
 * 답변 하나의 신뢰도 표시. trust 가 null 이면(거절, 답을 못 찾음) 보여 주지 않는다.
 * undefined 면 신뢰도 정보가 생기기 전에 저장한 답변이다.
 */
export function trustView(
  trust: Trust | null | undefined,
  legacy: { sources: Source[]; warnings: string[]; unverified: string[] },
): TrustView | null {
  if (trust === null) return null;
  if (trust === undefined) return fromLegacy(legacy.sources, legacy.warnings, legacy.unverified);
  return fromTrust(trust);
}
