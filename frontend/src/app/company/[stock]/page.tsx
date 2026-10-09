"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { BarChart, DataTable, LineChart, type Series } from "@/components/Chart";
import { DisclosureList } from "@/components/DisclosureList";
import { KpiTiles } from "@/components/Kpi";
import { Empty, ErrorBox, Loading } from "@/components/ui";
import { api, del, post } from "@/lib/api";
import { dartUrl, pct, won, wonShort } from "@/lib/format";
import { type CompanyDetail, METRIC_LABEL, type QuarterPoint } from "@/lib/types";

function WatchButton({ stock, initial }: { stock: string; initial: boolean }) {
  const [watched, setWatched] = useState(initial);
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="btn"
      disabled={busy}
      aria-pressed={watched}
      onClick={async () => {
        setBusy(true);
        try {
          if (watched) await del(`/api/watchlist/${stock}`);
          else await post("/api/watchlist", { stock, min_importance: 2 });
          setWatched(!watched);
        } finally {
          setBusy(false);
        }
      }}
    >
      {watched ? "관심 종목 해제" : "관심 종목에 추가"}
    </button>
  );
}

function ReportButton({ stock }: { stock: string }) {
  return (
    <details className="relative">
      <summary className="btn list-none">PDF 리포트</summary>
      <div className="absolute right-0 z-10 mt-1 w-64 rounded-lg border border-line bg-surface p-2 text-sm shadow-lg">
        <a className="block rounded px-2 py-1.5 hover:bg-surface-2" href={`/api/company/${stock}/report.pdf`} download>
          재무·공시 리포트 (바로)
        </a>
        <a className="block rounded px-2 py-1.5 hover:bg-surface-2" href={`/api/company/${stock}/report.pdf?llm=true`} download>
          사업 개요·위험 요약 포함 (1~3분)
        </a>
      </div>
    </details>
  );
}

const MONEY = ["revenue", "operating_income", "net_income"];
const MARGIN = ["operating_margin", "net_margin"];

export default function CompanyPage({ params }: { params: Promise<{ stock: string }> }) {
  const { stock } = use(params);
  const [data, setData] = useState<CompanyDetail | null>(null);
  const [quarters, setQuarters] = useState<QuarterPoint[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api<CompanyDetail>(`/api/company/${stock}`).then(setData).catch(setError);
    api<{ quarters: QuarterPoint[] }>(`/api/company/${stock}/quarters`)
      .then((r) => setQuarters(r.quarters))
      .catch(() => setQuarters([]));
  }, [stock]);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;

  const years = data.series.map((p) => String(p.year));
  const money: Series[] = MONEY.map((k) => ({ key: k, label: METRIC_LABEL[k], values: data.series.map((p) => p.values[k] ?? null) }));
  const margin: Series[] = MARGIN.map((k) => ({ key: k, label: METRIC_LABEL[k], values: data.series.map((p) => p.ratios[k] ?? null) }));
  const qLabels = (quarters ?? []).map((q) => `${String(q.year).slice(2)}.${q.quarter}Q${q.derived.length ? "*" : ""}`);
  const qSeries: Series[] = ["revenue", "operating_income"].map((k) => ({
    key: k,
    label: METRIC_LABEL[k],
    values: (quarters ?? []).map((q) => q.values[k] ?? null),
  }));
  const latest = data.series[data.series.length - 1];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">
          {data.corp_name} <span className="text-base font-normal text-muted">{data.stock_code}</span>
        </h1>
        <div className="flex flex-wrap gap-2">
          <Link className="btn" href={`/compare?stocks=${data.stock_code}`}>
            다른 회사와 비교
          </Link>
          <Link className="btn" href={`/diff?stock=${data.stock_code}`}>
            변경점
          </Link>
          <ReportButton stock={data.stock_code} />
          <WatchButton stock={data.stock_code} initial={data.watched} />
        </div>
      </div>

      {data.issues.length ? (
        <div role="note" className="rounded-xl border border-warning/40 bg-warning-soft px-4 py-3 text-sm">
          <strong className="text-warning">▲ 데이터 확인 필요</strong>
          <ul className="mt-1 list-disc pl-5">
            {data.issues.slice(0, 5).map((i) => (
              <li key={`${i.bsns_year}${i.reprt_code}${i.fs_div}${i.rule}`}>
                {i.bsns_year} {i.fs_div === "CFS" ? "연결" : "별도"} · {i.detail}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-muted">수집한 재무 수치가 검증 규칙에 걸렸습니다. 원문 공시와 비교해 보세요.</p>
        </div>
      ) : null}

      {data.series.length ? (
        <>
          <KpiTiles series={data.series} />
          <div className="grid gap-5 lg:grid-cols-2">
            <section className="card">
              <h2 className="mb-2 font-semibold">매출과 이익</h2>
              <BarChart categories={years} series={money} format={won} axisFormat={wonShort} title="연도별 매출액, 영업이익, 당기순이익" />
              <DataTable categories={years} series={money} format={won} caption="연도별 매출과 이익" />
              <p className="mt-2 text-xs text-muted">
                사업보고서 기준, {latest.fs_div ?? "연결"} 재무제표 우선 ·{" "}
                {latest.rcept_no ? (
                  <a className="link" href={dartUrl(latest.rcept_no)} target="_blank" rel="noopener noreferrer">
                    {latest.year} 사업보고서 원문
                  </a>
                ) : null}
              </p>
            </section>
            <section className="card">
              <h2 className="mb-2 font-semibold">이익률</h2>
              <LineChart categories={years} series={margin} format={(v) => pct(v)} title="연도별 영업이익률과 순이익률" />
              <DataTable categories={years} series={margin} format={(v) => pct(v)} caption="연도별 이익률" />
            </section>
          </div>
        </>
      ) : (
        <Empty>재무 데이터가 아직 없습니다. 작업자가 사업보고서를 수집하면 여기에 나타납니다.</Empty>
      )}

      <section className="card">
        <h2 className="mb-2 font-semibold">분기 실적</h2>
        {quarters === null ? (
          <Loading />
        ) : quarters.length ? (
          <>
            <LineChart categories={qLabels} series={qSeries} format={won} axisFormat={wonShort} title="분기별 매출액과 영업이익" />
            <DataTable categories={qLabels} series={qSeries} format={won} caption="분기별 실적" />
            {quarters.some((q) => q.derived.length) ? (
              <p className="mt-2 text-xs text-muted">* 4분기는 연간 금액에서 3분기 누적 금액을 빼서 계산했습니다.</p>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-muted">분기·반기 보고서 재무 데이터가 없습니다.</p>
        )}
      </section>

      <section className="card">
        <h2 className="mb-1 font-semibold">최근 공시</h2>
        {data.disclosures.length ? (
          <DisclosureList items={data.disclosures} showCompany={false} />
        ) : (
          <p className="text-sm text-muted">최근 90일 공시가 없습니다.</p>
        )}
      </section>
      <p className="text-xs text-muted">{data.disclaimer}</p>
    </div>
  );
}
