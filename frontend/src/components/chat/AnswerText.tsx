"use client";

// 답변 본문. [1] 같은 출처 번호를 누르면 그 출처를 펼친다.

export function AnswerText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const paragraphs = text.split(/\n{2,}/);
  return (
    <div className="space-y-2 leading-relaxed">
      {paragraphs.map((p, pi) => (
        <p key={pi} className="whitespace-pre-wrap">
          {p.split(/(\[\d+\])/g).map((part, i) => {
            const m = part.match(/^\[(\d+)\]$/);
            if (!m) return <span key={i}>{part}</span>;
            const n = Number(m[1]);
            return (
              <button
                key={i}
                type="button"
                onClick={() => onCite(n)}
                className="mx-0.5 rounded bg-accent-soft px-1 align-baseline text-xs font-semibold text-accent hover:underline"
                aria-label={`출처 ${n} 보기`}
              >
                {n}
              </button>
            );
          })}
        </p>
      ))}
    </div>
  );
}
