"use client";

import { trustView } from "@/lib/trust";
import type { Source, Trust } from "@/lib/types";
import { AnswerText } from "./AnswerText";
import { Feedback } from "./Feedback";
import { SourceList } from "./SourceList";
import { TrustSummary } from "./TrustSummary";
import { useSourcePanel } from "./useSourcePanel";

export interface Turn {
  key: string;
  question: string;
  resolved?: string; // 앞 질문을 이어받아 완성한 질문
  inherited?: string[];
  answer: string;
  sources: Source[];
  warnings: string[];
  unverified: string[];
  // 답변 신뢰도 (null: 거절했거나 답을 못 찾음, undefined: 이 정보가 생기기 전에 저장한 답변)
  trust?: Trust | null;
  found?: boolean;
  refused: string | null;
  cached: boolean;
  messageId: number | null;
  rating?: number | null;
  streaming: boolean;
  error?: string;
  model?: string;
  elapsedMs?: number;
}

export function TurnView({ turn }: { turn: Turn }) {
  const panel = useSourcePanel();
  const prefix = `turn-${turn.key}`;
  const shown = turn.sources.filter((s) => s.cited !== false);
  const numbers = new Set(shown.map((s, i) => s.number ?? i + 1));

  const finished = !turn.streaming && !turn.error && Boolean(turn.answer);
  const trust =
    finished && !turn.refused && turn.found !== false
      ? trustView(turn.trust, { sources: turn.sources, warnings: turn.warnings, unverified: turn.unverified })
      : null;

  return (
    <article className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-2.5 text-accent-ink">{turn.question}</div>
      </div>
      {turn.inherited?.length && turn.resolved && turn.resolved !== turn.question ? (
        <p className="text-right text-xs text-muted">
          앞 질문에서 {turn.inherited.join(", ")}을(를) 이어받아 “{turn.resolved}”로 찾았습니다.
        </p>
      ) : null}
      <div className={shown.length ? "grid items-start gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]" : ""}>
        <div className="card min-w-0">
          {turn.error ? (
            <p role="alert" className="text-sm text-critical">
              {turn.error}
            </p>
          ) : turn.answer ? (
            <AnswerText
              text={turn.answer}
              onCite={panel.cite}
              active={panel.open}
              sourceId={(n) => `${prefix}-src-${n}`}
              exists={(n) => numbers.has(n)}
            />
          ) : (
            <p className="animate-pulse text-sm text-muted">{turn.sources.length ? "답변을 쓰는 중…" : "공시를 찾는 중…"}</p>
          )}
          {turn.streaming && turn.answer ? <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent align-middle" aria-hidden /> : null}

          {trust ? <TrustSummary view={trust} id={`${prefix}-trust`} /> : null}

          {!turn.streaming && turn.messageId && !turn.refused ? <Feedback messageId={turn.messageId} initial={turn.rating} /> : null}
          {!turn.streaming && (turn.model || turn.cached) ? (
            <p className="mt-2 text-xs text-muted">
              {turn.cached ? "저장된 답변 · " : ""}
              {turn.model ? `모델 ${turn.model}` : ""}
              {turn.elapsedMs ? ` · ${(turn.elapsedMs / 1000).toFixed(1)}초` : ""}
            </p>
          ) : null}
        </div>

        <SourceList
          idPrefix={prefix}
          sources={turn.sources}
          open={panel.open}
          request={panel.request}
          onToggle={panel.toggle}
          onClose={panel.close}
          onReturn={panel.onReturn}
          // 큰 화면에서는 답변 옆에 붙어 있고, 질문 입력창(아래 14rem)에 가리지 않게 높이를 줄여 따로 스크롤한다
          className="xl:sticky xl:top-20 xl:max-h-[calc(100dvh-19rem)] xl:overflow-y-auto"
        />
      </div>
    </article>
  );
}
