"use client";

// 답변 본문. [1] 같은 출처 번호를 누르면 출처 패널에서 그 원문을 열고 인용한 문단을 표시한다.

export function AnswerText(props: {
  text: string;
  onCite: (n: number, el: HTMLButtonElement) => void;
  active: number | null; // 지금 패널에 열린 출처 번호
  sourceId: (n: number) => string;
  exists: (n: number) => boolean; // 출처 목록에 있는 번호인지 (없는 번호는 검증 경고로 따로 알린다)
}) {
  const paragraphs = props.text.split(/\n{2,}/);
  return (
    <div className="space-y-2 leading-relaxed">
      {paragraphs.map((p, pi) => (
        <p key={pi} className="whitespace-pre-wrap">
          {p.split(/(\[\d+\])/g).map((part, i) => {
            const m = part.match(/^\[(\d+)\]$/);
            if (!m) return <span key={i}>{part}</span>;
            const n = Number(m[1]);
            if (!props.exists(n)) {
              return (
                <span key={i} className="mx-0.5 rounded px-1 align-baseline text-xs text-muted line-through" title="출처 목록에 없는 번호">
                  {n}
                </span>
              );
            }
            const active = props.active === n;
            return (
              <button
                key={i}
                type="button"
                data-cite={n}
                onClick={(e) => props.onCite(n, e.currentTarget)}
                className={`mx-0.5 rounded px-1 align-baseline text-xs font-semibold hover:underline ${
                  active ? "bg-accent text-accent-ink" : "bg-accent-soft text-accent"
                }`}
                aria-label={`출처 ${n} 원문 보기`}
                aria-controls={props.sourceId(n)}
                aria-expanded={active}
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
