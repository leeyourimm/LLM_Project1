// 숫자 표기. 백엔드 fmt_won 과 같은 규칙(억 단위 반올림, 조·억).

export function won(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "-";
  const sign = amount < 0 ? "-" : "";
  const a = Math.abs(amount);
  const eokTotal = Math.round(a / 1e8);
  const jo = Math.floor(eokTotal / 10_000);
  const eok = eokTotal % 10_000;
  if (jo) return `${sign}${jo.toLocaleString("ko-KR")}조${eok ? ` ${eok.toLocaleString("ko-KR")}억원` : "원"}`;
  if (eok) return `${sign}${eok.toLocaleString("ko-KR")}억원`;
  return `${sign}${a.toLocaleString("ko-KR")}원`;
}

/** 차트 축처럼 짧게: 1.2조, 3,400억 */
export function wonShort(amount: number): string {
  const a = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}${(a / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조`;
  if (a >= 1e8) return `${sign}${Math.round(a / 1e8).toLocaleString("ko-KR")}억`;
  return `${sign}${a.toLocaleString("ko-KR")}`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "-";
  return `${v.toFixed(digits)}%`;
}

export function signedPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "-";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function dartUrl(rceptNo: string) {
  return `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${rceptNo}`;
}

export function shortDate(iso: string) {
  return iso.slice(0, 10);
}
