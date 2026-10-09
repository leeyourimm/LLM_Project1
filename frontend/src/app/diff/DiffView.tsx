"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { CompanyInput } from "@/components/CompanyInput";
import { toStock, useApp } from "@/components/providers";
import { ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api, qs } from "@/lib/api";
import { dartUrl, signedPct, won } from "@/lib/format";
import type { DiffDigest, DiffResult } from "@/lib/types";

const KINDS = ["사업보고서", "반기보고서", "분기보고서"];
const STATUS = { added: "새 섹션", removed: "삭제된 섹션", changed: "변경" } as const;

function Summary({ stock, kind }: { stock: string; kind: string }) {
  const [d, setD] = useState<DiffDigest | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setD(null);
    setError(null);
    api<DiffDigest>(`/api/diff/summary${qs({ stock, kind })}`).then(setD).catch(setError);
  }, [stock, kind]);
  if (error) return <ErrorBox error={error} />;
  if (!d) return <Loading label="요약을 만드는 중… 처음에는 1~2분 걸릴 수 있습니다." />;
  const metrics = d.metrics.filter((m) => m.before !== null && m.after !== null);
  const groups = Object.entries(d.points);
  return (
    <section className="card">
      <h2 className="font-semibold">한눈에 보기</h2>
      {groups.map(([key, points]) => (
        <div key={key} className="mt-3">
          <h3 className="text-sm font-semibold">{d.titles[key]}</h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
            {points.map((p) => (
              <li key={p.text}>
                {p.text}
                {p.unverified.length ? <span className="text-warning"> (숫자 확인 필요)</span> : null}{" "}
                <span className="text-xs text-muted">{p.refs.map((n) => `[${n}]`).join("")}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {metrics.length ? (
        <div className="mt-3">
          <h3 className="text-sm font-semibold">숫자 변화 (재무 데이터로 계산)</h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm tabular-nums">
            {metrics.map((m) => (
              <li key={m.key}>
                {m.label}: {won(m.before)} → {won(m.after)} {m.growth !== null ? `(${signedPct(m.growth)})` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {!groups.length && !metrics.length ? (
        <p className="mt-2 text-sm text-muted">{d.model ? "요약할 만한 변경을 찾지 못했습니다." : "답변 모델이 꺼져 있어 요약을 만들지 않았습니다."}</p>
      ) : null}
      {d.evidence.length ? (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-muted">근거 항목 {d.evidence.length}개</summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5">
            {d.evidence.map((e) => (
              <li key={e.number}>
                <span className="text-muted">
                  {e.section} · {e.kind}
                </span>{" "}
                {e.text}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      <p className="mt-3 text-xs text-muted">
        {d.model ? `요약 모델 ${d.model} · ` : ""}
        {d.disclaimer}
      </p>
    </section>
  );
}

export function DiffView() {
  const { companies } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const stock = params.get("stock") ?? "";
  const kind = params.get("kind") ?? "사업보고서";
  const [text, setText] = useState(stock);
  const [data, setData] = useState<DiffResult | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    if (!stock) return;
    api<DiffResult>(`/api/diff${qs({ stock, kind })}`).then(setData).catch(setError);
  }, [stock, kind]);

  const go = (s: string, k: string) => router.replace(`/diff${qs({ stock: s, kind: k })}`);
  const real = data?.sections.filter((s) => s.status !== "changed" || s.added.length || s.removed.length || s.modified.length) ?? [];
  const minor = data?.sections.filter((s) => !real.includes(s)) ?? [];

  return (
    <div className="space-y-4">
      <PageTitle title="보고서 변경점" sub="직전 보고서와 섹션별로 비교해 새로 생긴 위험, 빠진 내용, 숫자 변화를 보여 줍니다." />
      <form
        className="card flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const s = toStock(text, companies);
          if (!s) setError(new Error("회사를 찾지 못했습니다. 목록에서 골라 주세요."));
          else go(s, kind);
        }}
      >
        <CompanyInput value={text} onChange={setText} required className="min-w-56 flex-1" />
        <select className="input w-auto" value={kind} onChange={(e) => stock && go(stock, e.target.value)} aria-label="보고서 종류">
          {KINDS.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
        <button className="btn btn-primary" type="submit">
          최근 두 보고서 비교
        </button>
      </form>
      {error ? <ErrorBox error={error} /> : null}
      {stock && !data && !error ? <Loading label="비교하는 중…" /> : null}
      {data ? (
        <>
          <div className="card">
            <h2 className="font-semibold">{data.title}</h2>
            <p className="mt-1 text-sm">
              <a className="link" href={dartUrl(data.old.rcept_no)} target="_blank" rel="noopener noreferrer">
                {data.old.report_nm}
              </a>{" "}
              →{" "}
              <a className="link" href={dartUrl(data.new.rcept_no)} target="_blank" rel="noopener noreferrer">
                {data.new.report_nm}
              </a>
            </p>
          </div>
          <Summary stock={stock} kind={kind} />
          {real.map((s) => (
            <section key={s.key} className="card">
              <h3 className="font-semibold">
                {s.importance >= 2 ? <span className="text-warning">▲ </span> : null}
                {s.key}
              </h3>
              <p className="text-xs text-muted">
                {STATUS[s.status]} · 추가 {s.added.length} · 삭제 {s.removed.length} · 수정 {s.modified.length} · 숫자만 변경{" "}
                {s.numbers_only}
              </p>
              <ul className="mt-2 space-y-1.5 text-sm">
                {s.added.slice(0, 20).map((t) => (
                  <li key={`a${t}`} className="border-l-2 border-good pl-2">
                    <span className="sr-only">추가: </span>
                    {t}
                  </li>
                ))}
                {s.removed.slice(0, 20).map((t) => (
                  <li key={`r${t}`} className="border-l-2 border-critical pl-2 text-muted line-through">
                    <span className="sr-only">삭제: </span>
                    {t}
                  </li>
                ))}
                {s.modified.slice(0, 20).map((m) => (
                  <li key={`m${m.before}`} className="border-l-2 border-accent pl-2">
                    <div className="text-muted line-through">{m.before}</div>
                    <div>{m.after}</div>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {minor.length ? (
            <p className="card text-sm text-muted">숫자만 갱신된 섹션: {minor.map((s) => `${s.key} (${s.numbers_only})`).join(", ")}</p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
