"use client";

import { useState } from "react";
import { post } from "@/lib/api";

const REASONS = [
  ["wrong_number", "숫자가 틀림"],
  ["wrong_source", "출처가 맞지 않음"],
  ["not_found", "답을 못 찾음"],
  ["unhelpful", "도움이 안 됨"],
  ["other", "기타"],
] as const;

export function Feedback({ messageId, initial }: { messageId: number; initial?: number | null }) {
  const [rating, setRating] = useState<number | null>(initial ?? null);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<string>("wrong_number");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function send(r: 1 | -1, extra: { reason?: string; comment?: string } = {}) {
    setError(null);
    try {
      await post(`/api/messages/${messageId}/feedback`, { rating: r, ...extra });
      setRating(r);
      setAsking(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다");
    }
  }

  return (
    <div className="mt-3 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted">이 답변이 도움이 됐나요?</span>
        <button
          type="button"
          className={`btn px-2 py-1 ${rating === 1 ? "border-good text-good" : ""}`}
          aria-pressed={rating === 1}
          onClick={() => send(1)}
        >
          좋아요
        </button>
        <button
          type="button"
          className={`btn px-2 py-1 ${rating === -1 ? "border-critical text-critical" : ""}`}
          aria-pressed={rating === -1}
          onClick={() => setAsking((v) => !v)}
        >
          아쉬워요
        </button>
      </div>
      {asking ? (
        <form
          className="mt-2 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            send(-1, { reason, comment: comment || undefined });
          }}
        >
          <select className="input w-auto" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="이유">
            {REASONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <input
            className="input min-w-48 flex-1"
            placeholder="자세히 (선택)"
            maxLength={1000}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <button className="btn btn-primary" type="submit">
            보내기
          </button>
        </form>
      ) : null}
      {error ? <p className="mt-1 text-xs text-critical">{error}</p> : null}
    </div>
  );
}
