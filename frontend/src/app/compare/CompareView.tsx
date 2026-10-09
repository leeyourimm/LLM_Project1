"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { BarChart, DataTable, LineChart, type Series } from "@/components/Chart";
import { AnswerText } from "@/components/chat/AnswerText";
import { SourceList } from "@/components/chat/SourceList";
import { CompanyInput } from "@/components/CompanyInput";
import { toStock, useApp } from "@/components/providers";
import { Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api, post, qs } from "@/lib/api";
import { pct, won, wonShort } from "@/lib/format";
import { type CompareResult, METRIC_LABEL, type Source } from "@/lib/types";

const TOPICS = [
  ["business", "사업 구조"],
  ["risk", "위험 요인"],
  ["investment", "투자 계획"],
] as const;

const ROWS: { key: string; kind: "won" | "ratio" | "growth" }[] = [
  { key: "revenue", kind: "won" },
  { key: "operating_income", kind: "won" },
  { key: "net_income", kind: "won" },
  { key: "operating_margin", kind: "ratio" },
  { key: "net_margin", kind: "ratio" },
  { key: "debt_ratio", kind: "ratio" },
  { key: "revenue", kind: "growth" },
];

interface Summary {
  question: string;
  answer: string;
  warnings: string[];
  sources: Source[];
  model: string;
}

export function CompareView() {
  const { companies } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const stocks = (params.get("stocks") ?? "").split(",").filter((s) => /^\d{6}$/.test(s));
  const [text, setText] = useState("");
  const [data, setData] = useState<CompareResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [topic, setTopic] = useState<string>("business");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryError, setSummaryError] = useState<unknown>(null);
  const [open, setOpen] = useState<number | null>(null);

  const setStocks = (next: string[]) => router.replace(`/compare${next.length ? `?stocks=${next.join(",")}` : ""}`);
  const key = stocks.join(",");

  useEffect(() => {
    setData(null);
    setSummary(null);
    setError(null);
    if (stocks.length < 2) return;
    api<CompareResult>(`/api/compare${qs({ stocks })}`).then(setData).catch(setError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const name = (s: string) => companies.find((c) => c.stock_code === s)?.corp_name ?? s;

  async function summarize() {
    setSummaryBusy(true);
    setSummaryError(null);
    try {
      setSummary(await post<Summary>("/api/compare/summary", { stocks, topic }));
    } catch (e) {
      setSummaryError(e);
    } finally {
      setSummaryBusy(false);
    }
  }

  const names = data?.companies.map((c) => c.corp_name) ?? [];
  const barSeries: Series[] = data
    ? ["revenue", "operating_income"].map((k) => ({
        key: k,
        label: METRIC_LABEL[k],
        values: data.companies.map((c) => c.point?.values[k] ?? null),
      }))
    : [];
  const allYears = data ? [...new Set(data.companies.flatMap((c) => c.series.map((p) => p.year)))].sort() : [];
  const trend: Series[] = data
    ? data.companies.map((c) => ({
        key: c.stock_code,
        label: c.corp_name,
        values: allYears.map((y) => c.series.find((p) => p.year === y)?.ratios.operating_margin ?? null),
      }))
    : [];

  return (
    <div className="space-y-5">
      <PageTitle title="기업 비교" sub="2~5곳을 골라 같은 연도 기준으로 비교합니다." />
      <form
        className="card space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          const s = toStock(text, companies);
          if (!s) {
            setError(new Error("회사를 찾지 못했습니다. 목록에서 골라 주세요."));
            return;
          }
          if (!stocks.includes(s) && stocks.length < 5) setStocks([...stocks, s]);
          setText("");
        }}
      >
        <div className="flex flex-wrap items-end gap-2">
          <CompanyInput value={text} onChange={setText} className="min-w-56 flex-1" />
          <button className="btn btn-primary" type="submit" disabled={stocks.length >= 5}>
            추가
          </button>
        </div>
        {stocks.length ? (
          <ul className="flex flex-wrap gap-2">
            {stocks.map((s) => (
              <li key={s} className="flex items-center gap-1 rounded-full bg-accent-soft py-1 pr-1 pl-3 text-sm text-accent">
                {name(s)}
                <button
                  type="button"
                  className="rounded-full px-2 hover:bg-surface"
                  aria-label={`${name(s)} 빼기`}
                  onClick={() => setStocks(stocks.filter((x) => x !== s))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </form>

      {error ? <ErrorBox error={error} /> : null}
      {stocks.length < 2 ? (
        <Empty>비교할 회사를 두 곳 이상 추가하세요.</Empty>
      ) : !data ? (
        error ? null : <Loading />
      ) : (
        <>
          <section className="card overflow-x-auto">
            <h2 className="mb-2 font-semibold">{data.year ? `${data.year}년 사업보고서 기준` : "각 회사의 최근 사업보고서 기준"}</h2>
            <table className="w-full min-w-max text-sm tabular-nums">
              <thead>
                <tr className="border-b border-line text-muted">
                  <th className="py-2 pr-3 text-left font-medium" scope="col">
                    항목
                  </th>
                  {data.companies.map((c) => (
                    <th key={c.stock_code} className="px-3 py-2 text-right font-medium" scope="col">
                      {c.corp_name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.map((r) => (
                  <tr key={r.key + r.kind} className="border-b border-line last:border-0">
                    <th className="py-2 pr-3 text-left font-medium" scope="row">
                      {r.kind === "growth" ? "매출액 증가율" : METRIC_LABEL[r.key]}
                    </th>
                    {data.companies.map((c) => {
                      const p = c.point;
                      const v = !p ? null : r.kind === "won" ? p.values[r.key] : r.kind === "ratio" ? p.ratios[r.key] : p.growth[r.key];
                      return (
                        <td key={c.stock_code} className="px-3 py-2 text-right">
                          {r.kind === "won" ? won(v ?? null) : pct(v ?? null)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <div className="grid gap-5 lg:grid-cols-2">
            <section className="card">
              <h2 className="mb-2 font-semibold">매출과 영업이익</h2>
              <BarChart categories={names} series={barSeries} format={won} axisFormat={wonShort} title="회사별 매출액과 영업이익" />
              <DataTable categories={names} series={barSeries} format={won} caption="회사별 매출액과 영업이익" />
            </section>
            <section className="card">
              <h2 className="mb-2 font-semibold">영업이익률 추이</h2>
              <LineChart categories={allYears.map(String)} series={trend} format={(v) => pct(v)} title="회사별 연도별 영업이익률" />
              <DataTable categories={allYears.map(String)} series={trend} format={(v) => pct(v)} caption="회사별 영업이익률" />
            </section>
          </div>

          <section className="card">
            <h2 className="mb-2 font-semibold">공시 본문 비교</h2>
            <div className="flex flex-wrap items-center gap-2">
              <div role="radiogroup" aria-label="비교 주제" className="flex gap-1">
                {TOPICS.map(([v, l]) => (
                  <button
                    key={v}
                    type="button"
                    role="radio"
                    aria-checked={topic === v}
                    className={`btn ${topic === v ? "border-accent text-accent" : ""}`}
                    onClick={() => setTopic(v)}
                  >
                    {l}
                  </button>
                ))}
              </div>
              <button type="button" className="btn btn-primary" onClick={summarize} disabled={summaryBusy}>
                {summaryBusy ? "공시를 읽는 중…" : "비교 설명 받기"}
              </button>
            </div>
            {summaryError ? (
              <div className="mt-3">
                <ErrorBox error={summaryError} />
              </div>
            ) : null}
            {summary ? (
              <div className="mt-4">
                <AnswerText text={summary.answer} onCite={setOpen} />
                {summary.warnings.length ? (
                  <p className="mt-2 rounded-lg bg-warning-soft px-3 py-2 text-sm">{summary.warnings.join(" / ")}</p>
                ) : null}
                <SourceList sources={summary.sources} open={open} onToggle={(n) => setOpen(open === n ? null : n)} />
                <p className="mt-2 text-xs text-muted">모델 {summary.model}</p>
              </div>
            ) : null}
          </section>
          <p className="text-xs text-muted">{data.disclaimer}</p>
        </>
      )}
    </div>
  );
}
