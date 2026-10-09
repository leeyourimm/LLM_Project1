"use client";

import type { Source } from "@/lib/types";

export function SourceList(props: { sources: Source[]; open: number | null; onToggle: (n: number) => void }) {
  if (!props.sources.length) return null;
  const cited = props.sources.filter((s) => s.cited !== false);
  const rest = props.sources.length - cited.length;
  return (
    <div className="mt-3">
      <h4 className="mb-1.5 text-xs font-semibold text-muted">출처</h4>
      <ol className="space-y-1.5">
        {cited.map((s, idx) => {
          const n = s.number ?? idx + 1;
          const isOpen = props.open === n;
          return (
            <li key={s.chunk_id} id={`src-${n}`} className="rounded-lg border border-line bg-surface-2/60 text-sm">
              <button
                type="button"
                className="flex w-full items-start gap-2 px-3 py-2 text-left"
                aria-expanded={isOpen}
                onClick={() => props.onToggle(n)}
              >
                <span className="mt-0.5 shrink-0 rounded bg-accent-soft px-1.5 text-xs font-semibold text-accent">{n}</span>
                <span className="min-w-0">
                  <span className="font-medium">{s.corp_name ?? ""}</span>{" "}
                  <span className="text-muted">
                    {s.report_nm ?? ""} · {s.section || "재무 데이터"}
                  </span>
                </span>
              </button>
              {isOpen ? (
                <div className="border-t border-line px-3 py-2">
                  {s.unit ? <div className="mb-1 text-xs text-muted">단위: {s.unit}</div> : null}
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-sans text-[13px] leading-relaxed">{s.body}</pre>
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noopener noreferrer" className="link mt-1 inline-block text-xs">
                      DART 원문 열기
                    </a>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      {rest > 0 ? <p className="mt-1 text-xs text-muted">검색했지만 답변에 쓰지 않은 문서 {rest}개는 숨겼습니다.</p> : null}
    </div>
  );
}
