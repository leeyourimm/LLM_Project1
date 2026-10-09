"use client";

// 의존성 없는 SVG 차트. 색은 --series-1.. 토큰, 글자는 본문 색 토큰만 쓴다.
// 막대: 기준선에서 먼 쪽만 4px 둥글게, 막대 사이 2px 간격. 선: 2px, 점 8px.
// 마우스를 올리면(키보드로 초점을 옮겨도) 값 상자가 뜬다. 범례는 시리즈가 둘 이상일 때만.

import { useEffect, useId, useMemo, useRef, useState } from "react";

export interface Series {
  key: string;
  label: string;
  values: (number | null)[];
}

interface Props {
  categories: string[];
  series: Series[];
  format: (v: number) => string;
  axisFormat?: (v: number) => string;
  height?: number;
  title: string; // 화면 읽기 프로그램용 설명
}

const M = { top: 12, right: 12, bottom: 26, left: 56 };
const DEFAULT_W = 640;

// 그림을 확대·축소하지 않고 실제 폭으로 그려야 글자 크기가 화면마다 같다
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(DEFAULT_W);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setW(Math.max(Math.round(el.clientWidth), 200));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceStep(span: number, count: number) {
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

export function ticks(values: (number | null)[], count = 4): number[] {
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  let lo = Math.min(0, ...nums);
  let hi = Math.max(0, ...nums);
  if (lo === hi) hi = lo + 1;
  const step = niceStep(hi - lo, count);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const out: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.round(v / step) * step);
  return out;
}

function barPath(x: number, yBase: number, yEnd: number, w: number) {
  const h = Math.abs(yEnd - yBase);
  const r = Math.min(4, w / 2, h);
  if (yEnd <= yBase) {
    return `M${x},${yBase}V${yEnd + r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd + r}V${yBase}Z`;
  }
  return `M${x},${yBase}V${yEnd - r}Q${x},${yEnd} ${x + r},${yEnd}H${x + w - r}Q${x + w},${yEnd} ${x + w},${yEnd - r}V${yBase}Z`;
}

const color = (i: number) => `var(--series-${(i % 3) + 1})`;

function useScale(series: Series[], height: number) {
  return useMemo(() => {
    const t = ticks(series.flatMap((s) => s.values));
    const lo = t[0];
    const hi = t[t.length - 1];
    const innerH = height - M.top - M.bottom;
    const y = (v: number) => M.top + innerH - ((v - lo) / (hi - lo)) * innerH;
    return { t, y };
  }, [series, height]);
}

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {series.map((s, i) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="inline-block size-2.5 rounded-sm" style={{ background: color(i) }} aria-hidden />
          {s.label}
        </li>
      ))}
    </ul>
  );
}

function Tooltip(props: {
  x: number;
  w: number;
  category: string;
  rows: { label: string; value: string; i: number }[];
}) {
  // 가장자리에서 상자가 잘리지 않게 안쪽으로 붙인다
  const left = `${Math.min(Math.max(props.x / props.w, 0.15), 0.85) * 100}%`;
  return (
    <div
      role="status"
      className="pointer-events-none absolute top-0 z-10 min-w-36 -translate-x-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg"
      style={{ left }}
    >
      <div className="mb-1 font-semibold">{props.category}</div>
      {props.rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-1.5 text-muted">
            <span className="inline-block size-2 rounded-sm" style={{ background: color(r.i) }} aria-hidden />
            {r.label}
          </span>
          <span className="font-medium tabular-nums">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

function Grid({ t, y, w, fmt }: { t: number[]; y: (v: number) => number; w: number; fmt: (v: number) => string }) {
  return (
    <g>
      {t.map((v) => (
        <g key={v}>
          <line x1={M.left} x2={w - M.right} y1={y(v)} y2={y(v)} stroke="var(--grid)" strokeWidth={v === 0 ? 0 : 1} />
          <text x={M.left - 8} y={y(v)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)">
            {fmt(v)}
          </text>
        </g>
      ))}
      <line x1={M.left} x2={w - M.right} y1={y(0)} y2={y(0)} stroke="var(--text)" strokeOpacity={0.5} />
    </g>
  );
}

export function BarChart({ categories, series, format, axisFormat, height = 240, title }: Props) {
  const { t, y } = useScale(series, height);
  const [hover, setHover] = useState<number | null>(null);
  const [box, w] = useWidth();
  const innerW = w - M.left - M.right;
  const band = innerW / Math.max(categories.length, 1);
  const groupW = Math.min(band * 0.7, 28 * series.length + 2 * (series.length - 1));
  const barW = (groupW - 2 * (series.length - 1)) / series.length;
  const tipRows = (ci: number) =>
    series.map((s, i) => ({ label: s.label, value: s.values[ci] === null ? "-" : format(s.values[ci] as number), i }));

  return (
    <figure>
      <Legend series={series} />
      <div className="relative" ref={box}>
        <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} className="block max-w-full" role="img" aria-label={title}>
          <Grid t={t} y={y} w={w} fmt={axisFormat ?? format} />
          {categories.map((c, ci) => {
            const x0 = M.left + band * ci + (band - groupW) / 2;
            return (
              <g key={c}>
                {series.map((s, si) => {
                  const v = s.values[ci];
                  if (v === null) return null;
                  return (
                    <path
                      key={s.key}
                      d={barPath(x0 + si * (barW + 2), y(0), y(v), barW)}
                      fill={color(si)}
                      opacity={hover === null || hover === ci ? 1 : 0.45}
                    />
                  );
                })}
                <text x={M.left + band * ci + band / 2} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">
                  {c}
                </text>
                {/* 막대보다 넓은 마우스·키보드 영역 */}
                <rect
                  x={M.left + band * ci}
                  y={M.top}
                  width={band}
                  height={height - M.top - M.bottom}
                  fill="transparent"
                  tabIndex={0}
                  aria-label={`${c}: ${tipRows(ci).map((r) => `${r.label} ${r.value}`).join(", ")}`}
                  onMouseEnter={() => setHover(ci)}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(ci)}
                  onBlur={() => setHover(null)}
                />
              </g>
            );
          })}
        </svg>
        {hover !== null ? (
          <Tooltip x={M.left + band * hover + band / 2} w={w} category={categories[hover]} rows={tipRows(hover)} />
        ) : null}
      </div>
    </figure>
  );
}

export function LineChart({ categories, series, format, axisFormat, height = 220, title }: Props) {
  const { t, y } = useScale(series, height);
  const [hover, setHover] = useState<number | null>(null);
  const clip = useId();
  const [box, w] = useWidth();
  const innerW = w - M.left - M.right;
  const step = categories.length > 1 ? innerW / (categories.length - 1) : 0;
  const x = (i: number) => (categories.length > 1 ? M.left + step * i : M.left + innerW / 2);
  const tipRows = (ci: number) =>
    series.map((s, i) => ({ label: s.label, value: s.values[ci] === null ? "-" : format(s.values[ci] as number), i }));
  // 라벨이 겹치지 않게 많으면 건너뛴다
  const every = Math.ceil(categories.length / 8);

  return (
    <figure>
      <Legend series={series} />
      <div className="relative" ref={box}>
        <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} className="block max-w-full" role="img" aria-label={title}>
          <defs>
            <clipPath id={clip}>
              <rect x={M.left - 6} y={0} width={innerW + 12} height={height} />
            </clipPath>
          </defs>
          <Grid t={t} y={y} w={w} fmt={axisFormat ?? format} />
          {hover !== null ? (
            <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={height - M.bottom} stroke="var(--muted)" strokeDasharray="3 3" />
          ) : null}
          <g clipPath={`url(#${clip})`}>
            {series.map((s, si) => {
              // 값이 빠진 곳에서는 선을 끊는다
              let d = "";
              s.values.forEach((v, i) => {
                if (v === null) return;
                const prev = i > 0 ? s.values[i - 1] : null;
                d += `${prev === null || i === 0 ? "M" : "L"}${x(i)},${y(v)}`;
              });
              return (
                <g key={s.key}>
                  <path d={d} fill="none" stroke={color(si)} strokeWidth={2} strokeLinejoin="round" />
                  {s.values.map((v, i) =>
                    v === null ? null : (
                      <circle
                        key={i}
                        cx={x(i)}
                        cy={y(v)}
                        r={hover === i ? 5 : 4}
                        fill={color(si)}
                        stroke="var(--surface)"
                        strokeWidth={2}
                      />
                    ),
                  )}
                </g>
              );
            })}
          </g>
          {categories.map((c, i) => (
            <g key={c}>
              {i % every === 0 || i === categories.length - 1 ? (
                <text
                  x={x(i)}
                  y={height - 8}
                  // 양 끝 라벨이 그림 밖으로 잘리지 않게
                  textAnchor={categories.length > 1 && i === categories.length - 1 ? "end" : i === 0 && categories.length > 1 ? "start" : "middle"}
                  fontSize={11}
                  fill="var(--muted)"
                >
                  {c}
                </text>
              ) : null}
              <rect
                x={x(i) - Math.max(step, 24) / 2}
                y={M.top}
                width={Math.max(step, 24)}
                height={height - M.top - M.bottom}
                fill="transparent"
                tabIndex={0}
                aria-label={`${c}: ${tipRows(i).map((r) => `${r.label} ${r.value}`).join(", ")}`}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
              />
            </g>
          ))}
        </svg>
        {hover !== null ? <Tooltip x={x(hover)} w={w} category={categories[hover]} rows={tipRows(hover)} /> : null}
      </div>
    </figure>
  );
}

export function DataTable(props: { categories: string[]; series: Series[]; format: (v: number) => string; caption: string }) {
  return (
    <details className="mt-2 text-sm">
      <summary className="cursor-pointer text-muted">표로 보기</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-max text-right tabular-nums">
          <caption className="sr-only">{props.caption}</caption>
          <thead>
            <tr className="border-b border-line text-muted">
              <th className="py-1.5 pr-3 text-left font-medium" scope="col">
                항목
              </th>
              {props.categories.map((c) => (
                <th key={c} className="px-2 py-1.5 font-medium" scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {props.series.map((s) => (
              <tr key={s.key} className="border-b border-line last:border-0">
                <th className="py-1.5 pr-3 text-left font-medium" scope="row">
                  {s.label}
                </th>
                {s.values.map((v, i) => (
                  <td key={i} className="px-2 py-1.5">
                    {v === null ? "-" : props.format(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
