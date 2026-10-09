import { pct, signedPct, won } from "@/lib/format";
import type { YearPoint } from "@/lib/types";

const KPI = [
  { key: "revenue", label: "매출액", kind: "won" },
  { key: "operating_income", label: "영업이익", kind: "won" },
  { key: "net_income", label: "당기순이익", kind: "won" },
  { key: "operating_margin", label: "영업이익률", kind: "ratio" },
  { key: "debt_ratio", label: "부채비율", kind: "ratio" },
] as const;

export function KpiTiles({ series }: { series: YearPoint[] }) {
  if (!series.length) return null;
  const cur = series[series.length - 1];
  const prev = series.length > 1 && series[series.length - 2].year === cur.year - 1 ? series[series.length - 2] : null;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {KPI.map((k) => {
        const v = k.kind === "won" ? cur.values[k.key] : cur.ratios[k.key];
        const pv = prev ? (k.kind === "won" ? prev.values[k.key] : prev.ratios[k.key]) : null;
        let delta: string;
        let tone = "text-muted";
        if (v === null || v === undefined || pv === null || pv === undefined) delta = "전년 비교 불가";
        else if (k.kind === "won") {
          const g = cur.growth[k.key];
          delta = g === null || g === undefined ? "전년 비교 불가" : `전년 대비 ${signedPct(g)}`;
          if (g) tone = g > 0 ? "text-good" : "text-critical";
        } else {
          const d = v - pv;
          delta = `전년 대비 ${d > 0 ? "+" : ""}${d.toFixed(1)}%p`;
        }
        return (
          <div key={k.key} className="card p-4">
            <div className="text-xs text-muted">
              {k.label} · {cur.year}
            </div>
            <div className="mt-1 text-lg font-bold tabular-nums">{k.kind === "won" ? won(v ?? null) : pct(v ?? null)}</div>
            <div className={`mt-0.5 text-xs ${tone}`}>{delta}</div>
          </div>
        );
      })}
    </div>
  );
}
