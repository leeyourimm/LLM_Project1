"use client";

import { useState } from "react";
import type { Source } from "@/lib/types";
import { AnswerText } from "./AnswerText";
import { Feedback } from "./Feedback";
import { SourceList } from "./SourceList";

export interface Turn {
  key: string;
  question: string;
  resolved?: string; // 앞 질문을 이어받아 완성한 질문
  inherited?: string[];
  answer: string;
  sources: Source[];
  warnings: string[];
  unverified: string[];
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
  const [open, setOpen] = useState<number | null>(null);
  const cite = (n: number) => {
    setOpen(n);
    requestAnimationFrame(() => document.getElementById(`src-${n}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  };
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
      <div className="card">
        {turn.error ? (
          <p role="alert" className="text-sm text-critical">
            {turn.error}
          </p>
        ) : turn.answer ? (
          <AnswerText text={turn.answer} onCite={cite} />
        ) : (
          <p className="animate-pulse text-sm text-muted">{turn.sources.length ? "답변을 쓰는 중…" : "공시를 찾는 중…"}</p>
        )}
        {turn.streaming && turn.answer ? <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent align-middle" aria-hidden /> : null}

        {!turn.streaming && (turn.warnings.length || turn.unverified.length) ? (
          <div className="mt-3 rounded-lg bg-warning-soft px-3 py-2 text-sm">
            <strong className="text-warning">확인 필요</strong>
            <ul className="mt-1 list-disc pl-5">
              {turn.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
              {turn.unverified.length ? <li>출처에서 확인되지 않은 숫자: {turn.unverified.join(", ")}</li> : null}
            </ul>
          </div>
        ) : null}

        <SourceList sources={turn.sources} open={open} onToggle={(n) => setOpen(open === n ? null : n)} />

        {!turn.streaming && turn.messageId && !turn.refused ? <Feedback messageId={turn.messageId} initial={turn.rating} /> : null}
        {!turn.streaming && (turn.model || turn.cached) ? (
          <p className="mt-2 text-xs text-muted">
            {turn.cached ? "저장된 답변 · " : ""}
            {turn.model ? `모델 ${turn.model}` : ""}
            {turn.elapsedMs ? ` · ${(turn.elapsedMs / 1000).toFixed(1)}초` : ""}
          </p>
        ) : null}
      </div>
    </article>
  );
}
