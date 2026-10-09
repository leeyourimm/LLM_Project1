"use client";

// 근거 원문 패널. 출처를 펼치면 모델이 읽은 원문(앞뒤 문단 포함)을 보여 주고,
// 답변이 인용한 문단과 답변에 옮긴 숫자를 표시한다. 큰 화면에서는 답변 옆에, 작은 화면에서는 답변 아래에 둔다.

import { useEffect, useRef, useState } from "react";
import { type Part, passageOf } from "@/lib/passage";
import type { Source } from "@/lib/types";

function Parts({ parts }: { parts: Part[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.quote ? (
          <strong key={i} className="rounded-sm bg-highlight-strong font-semibold underline decoration-2 underline-offset-2">
            {p.text}
          </strong>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

type Span = [number, number];

// [top, bottom] 안에 보이게 하려면 얼마나 스크롤해야 하나. 다 들어가는 첫 구간을 고르고, 없으면 마지막 구간의 위를 맞춘다.
function scrollDelta(spans: Span[], top: number, bottom: number): number {
  const [a, b] = spans.find(([a, b]) => b - a <= bottom - top) ?? spans[spans.length - 1];
  return a < top || b - a > bottom - top ? a - top : b > bottom ? b - bottom : 0;
}

// 펼친 출처를 보여 준다. 다 들어가면 출처 전체, 아니면 제목부터 인용 문단까지, 그것도 안 되면(휴대폰) 인용 문단만.
// 아래에 붙은 질문 입력창 자리는 li 의 scroll-margin-bottom 으로, 위는 scroll-margin-top 과
// 고정 머리글(휴대폰에서는 여러 줄) 중 큰 쪽만큼 비운다. 큰 화면에서 패널이 따로 스크롤되면 패널부터 움직인다.
function reveal(item: HTMLElement, mark: HTMLElement | null, behavior: ScrollBehavior) {
  const style = getComputedStyle(item);
  const header = document.querySelector("header");
  const stuck = header && ["sticky", "fixed"].includes(getComputedStyle(header).position);
  const top = Math.max(parseFloat(style.scrollMarginTop) || 0, stuck ? header.getBoundingClientRect().bottom + 8 : 0);
  const bottom = window.innerHeight - (parseFloat(style.scrollMarginBottom) || 0);
  const li = item.getBoundingClientRect();
  const m = mark?.getBoundingClientRect();
  const spans: Span[] = [[li.top, li.bottom]];
  if (m) spans.push([li.top, m.bottom], [m.top, m.bottom]);

  const panel = item.closest<HTMLElement>("[data-source-panel]");
  if (panel && panel.scrollHeight > panel.clientHeight + 1) {
    const r = panel.getBoundingClientRect();
    const d = scrollDelta(spans, Math.max(r.top, top) + 8, Math.min(r.bottom, bottom) - 8);
    if (Math.abs(d) >= 1) {
      panel.scrollBy({ top: d, behavior });
      for (const span of spans) (span[0] -= d), (span[1] -= d);
    }
  }
  const d = scrollDelta(spans, top, bottom);
  if (Math.abs(d) >= 1) window.scrollBy({ top: d, behavior });
}

function SourceItem(props: {
  id: string;
  n: number;
  source: Source;
  isOpen: boolean;
  request: number; // 답변의 [n] 으로 열었을 때 바뀌는 값 → 스크롤, 포커스, 강조
  onToggle: () => void;
  onClose: () => boolean; // 답변으로 포커스를 돌려줬으면 true
  onReturn: (() => void) | null;
}) {
  const { id, n, source: s, isOpen } = props;
  const itemRef = useRef<HTMLLIElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const markRef = useRef<HTMLElement>(null);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    if (!props.request) return;
    const box = boxRef.current;
    const mark = markRef.current;
    if (box && mark) {
      // 원문 상자 안에서 인용 문단이 보이게 (짧으면 가운데로)
      const room = box.clientHeight - mark.offsetHeight;
      box.scrollTop = Math.max(0, mark.offsetTop - (room > 0 ? room / 2 : 8));
    }
    if (itemRef.current) reveal(itemRef.current, mark, prefersReducedMotion() ? "auto" : "smooth");
    box?.focus({ preventScroll: true });
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 1300);
    return () => clearTimeout(t);
  }, [props.request]);

  const p = passageOf(s);
  const hasQuotes = [...p.before, ...p.cited, ...p.after].some((x) => x.quote);
  const title = `${s.corp_name ?? ""} ${s.report_nm ?? ""}`.trim();
  const meta = [s.unit ? `단위: ${s.unit}` : null, s.rcept_dt ? `${s.rcept_dt} 접수` : null].filter(Boolean).join(" · ");

  return (
    <li
      ref={itemRef}
      id={id}
      className={`scroll-mt-20 scroll-mb-56 rounded-lg border text-sm ${isOpen ? "border-accent/50 bg-surface-2" : "border-line bg-surface-2/60"}`}
      onKeyDown={(e) => {
        if (e.key === "Escape" && isOpen) {
          e.stopPropagation();
          if (!props.onClose()) toggleRef.current?.focus();
        }
      }}
    >
      <button
        ref={toggleRef}
        type="button"
        className="flex w-full items-start gap-2 px-3 py-2 text-left"
        aria-expanded={isOpen}
        aria-controls={`${id}-body`}
        onClick={props.onToggle}
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
        <div id={`${id}-body`} className="space-y-2 border-t border-line px-3 py-2">
          {meta ? <p className="text-xs text-muted">{meta}</p> : null}
          <div
            ref={boxRef}
            role="region"
            tabIndex={0}
            aria-label={`출처 ${n} 원문: ${title}`}
            data-testid="source-passage"
            className="relative max-h-80 overflow-auto rounded-md border border-line bg-surface px-3 py-2 text-[13px] leading-relaxed break-words whitespace-pre-wrap"
          >
            {p.before.length ? (
              <span className="text-muted">
                <Parts parts={p.before} />
              </span>
            ) : null}
            <mark
              ref={markRef}
              data-testid="cited-passage"
              className={`rounded-sm bg-highlight px-0.5 text-ink box-decoration-clone ${flash ? "cite-flash" : ""}`}
            >
              <span className="sr-only">[인용한 부분 시작] </span>
              <Parts parts={p.cited} />
              <span className="sr-only"> [인용한 부분 끝]</span>
            </mark>
            {p.after.length ? (
              <span className="text-muted">
                <Parts parts={p.after} />
              </span>
            ) : null}
          </div>
          <p className="text-xs text-muted">
            <mark className="rounded-sm bg-highlight px-1 text-ink">표시한 부분</mark>이 답변이 인용한 문단입니다
            {p.hasContext ? " (흐린 글은 앞뒤 문단)" : ""}
            {hasQuotes ? (
              <>
                {" · "}
                <strong className="rounded-sm bg-highlight-strong px-0.5 font-semibold text-ink underline decoration-2 underline-offset-2">
                  굵은 밑줄
                </strong>
                은 답변에 옮긴 숫자
              </>
            ) : null}
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            {s.url ? (
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="link">
                DART 원문 열기
                <span className="sr-only"> (새 창)</span>
              </a>
            ) : null}
            {props.onReturn ? (
              <button type="button" className="link" onClick={props.onReturn}>
                답변으로 돌아가기
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

export function SourceList(props: {
  idPrefix: string;
  sources: Source[];
  open: number | null;
  request: { n: number; seq: number } | null;
  onToggle: (n: number) => void;
  onClose: () => boolean;
  onReturn: (() => void) | null;
  className?: string;
}) {
  if (!props.sources.length) return null;
  const answered = props.sources.some((s) => s.cited !== undefined);
  const cited = props.sources.filter((s) => s.cited !== false);
  const rest = props.sources.length - cited.length;
  const titleId = `${props.idPrefix}-sources`;
  return (
    <section aria-labelledby={titleId} data-source-panel className={`card p-3 sm:p-4 ${props.className ?? ""}`}>
      <h3 id={titleId} className="mb-2 flex items-baseline justify-between gap-2 text-sm font-semibold">
        근거 원문
        <span className="text-xs font-normal text-muted">{answered ? `인용한 출처 ${cited.length}개` : `찾은 문서 ${cited.length}개`}</span>
      </h3>
      <ol className="space-y-1.5">
        {cited.map((s, idx) => {
          const n = s.number ?? idx + 1;
          return (
            <SourceItem
              key={s.chunk_id}
              id={`${props.idPrefix}-src-${n}`}
              n={n}
              source={s}
              isOpen={props.open === n}
              request={props.request?.n === n ? props.request.seq : 0}
              onToggle={() => props.onToggle(n)}
              onClose={props.onClose}
              onReturn={props.open === n ? props.onReturn : null}
            />
          );
        })}
      </ol>
      {rest > 0 ? <p className="mt-2 text-xs text-muted">검색했지만 답변에 쓰지 않은 문서 {rest}개는 숨겼습니다.</p> : null}
    </section>
  );
}
