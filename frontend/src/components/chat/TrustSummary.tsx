"use client";

// 답변 신뢰도 표시: 근거 수, 최신 공시 여부, 검증 경고. 점수 대신 확인한 사실을 짧게 보여 주고
// "자세히"에서 문장으로 풀어 쓴다. 답변 내용의 근거가 흔들리는 경고가 있으면 처음부터 펼쳐 둔다.

import { useState } from "react";
import type { Tone, TrustView } from "@/lib/trust";

const TONE: Record<Tone, { chip: string; ink: string; icon: string; label: string }> = {
  good: { chip: "bg-good-soft text-good", ink: "text-good", icon: "✓", label: "확인됨" },
  warn: { chip: "bg-warning-soft text-warning", ink: "text-warning", icon: "▲", label: "확인 필요" },
  info: { chip: "bg-surface-2 text-muted", ink: "text-muted", icon: "○", label: "참고" },
};

export function TrustSummary({ view, id }: { view: TrustView; id: string }) {
  const [open, setOpen] = useState(view.expand);
  return (
    <section aria-labelledby={`${id}-title`} data-testid="trust" className="mt-3 rounded-lg border border-line px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <h4 id={`${id}-title`} className="text-xs font-semibold text-muted">
          근거 점검
        </h4>
        <button
          type="button"
          className="link text-xs"
          aria-expanded={open}
          aria-controls={`${id}-details`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "접기" : "자세히"}
        </button>
      </div>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {view.chips.map((c) => (
          <li key={c.key} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${TONE[c.tone].chip}`}>
            <span aria-hidden>{TONE[c.tone].icon}</span>
            <span className="sr-only">{TONE[c.tone].label}: </span>
            {c.text}
          </li>
        ))}
      </ul>
      {open ? (
        <div id={`${id}-details`} className="mt-2 border-t border-line pt-2">
          <ul className="space-y-1 text-sm">
            {view.details.map((d) => (
              <li key={d.key} className="flex gap-2">
                <span aria-hidden className={`shrink-0 ${TONE[d.tone].ink}`}>
                  {TONE[d.tone].icon}
                </span>
                <span>
                  <span className="sr-only">{TONE[d.tone].label}: </span>
                  {d.text}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted">
            {view.checkedOn ? `${view.checkedOn} 답변 시점 기준입니다. ` : ""}
            점수가 아니라 서비스가 출처와 공시 목록에서 확인한 사실입니다.
          </p>
        </div>
      ) : null}
    </section>
  );
}
