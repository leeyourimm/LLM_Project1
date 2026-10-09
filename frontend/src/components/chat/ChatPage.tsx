"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, del } from "@/lib/api";
import { streamPost } from "@/lib/sse";
import type { AnswerDone, ConversationSummary, Source, StoredMessage } from "@/lib/types";
import { CompanyInput } from "../CompanyInput";
import { toStock, useApp } from "../providers";
import { ErrorBox } from "../ui";
import { type Turn, TurnView } from "./Message";

function turnsFrom(messages: StoredMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const m of messages) {
    if (m.role === "user") {
      turns.push({
        key: `m${m.id}`,
        question: m.content,
        resolved: m.payload.resolved_question,
        inherited: m.payload.inherited,
        answer: "",
        sources: [],
        warnings: [],
        unverified: [],
        refused: null,
        cached: false,
        messageId: null,
        streaming: false,
      });
    } else if (turns.length) {
      const t = turns[turns.length - 1];
      const p = m.payload;
      Object.assign(t, {
        answer: m.content,
        sources: p.sources ?? [],
        warnings: p.warnings ?? [],
        unverified: p.unverified_numbers ?? [],
        trust: p.trust,
        found: p.found,
        refused: p.refused ?? null,
        cached: p.cached ?? false,
        messageId: m.id,
        rating: m.rating,
        model: p.model,
        elapsedMs: p.elapsed_ms,
      });
    }
  }
  return turns;
}

export function ChatPage() {
  const { companies } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const conversationId = params.get("c") ? Number(params.get("c")) : null;

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [company, setCompany] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const abort = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  // 방금 스트리밍으로 만든 대화는 다시 불러오지 않는다
  const skipLoad = useRef<number | null>(null);

  // 예시 질문은 서버 설정(EXAMPLE_QUESTIONS) 한 곳에서 온다. 서버가 답을 미리 캐시해 두어 바로 답한다
  const [examples, setExamples] = useState<string[]>([]);

  const loadConversations = useCallback(() => {
    api<ConversationSummary[]>("/api/conversations").then(setConversations).catch(() => setConversations([]));
  }, []);

  useEffect(loadConversations, [loadConversations]);

  useEffect(() => {
    api<{ questions: string[] }>("/api/examples")
      .then((r) => setExamples(r.questions))
      .catch(() => setExamples([]));
  }, []);

  useEffect(() => {
    if (conversationId === null) {
      setTurns([]);
      return;
    }
    if (skipLoad.current === conversationId) return;
    api<{ messages: StoredMessage[] }>(`/api/conversations/${conversationId}`)
      .then((r) => setTurns(turnsFrom(r.messages)))
      .catch((e) => setError(e));
  }, [conversationId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns.length]);

  const update = (key: string, patch: Partial<Turn>) =>
    setTurns((ts) => ts.map((t) => (t.key === key ? { ...t, ...patch } : t)));

  async function ask(q: string) {
    const text = q.trim();
    if (text.length < 2 || busy) return;
    setError(null);
    const stock = company ? toStock(company, companies) : null;
    if (company && !stock) {
      setError(new Error("회사를 찾지 못했습니다. 목록에서 골라 주세요."));
      return;
    }
    const key = `t${Date.now()}`;
    setTurns((ts) => [
      ...ts,
      {
        key,
        question: text,
        answer: "",
        sources: [],
        warnings: [],
        unverified: [],
        refused: null,
        cached: false,
        messageId: null,
        streaming: true,
      },
    ]);
    setQuestion("");
    setBusy(true);
    abort.current = new AbortController();
    let answer = "";
    try {
      for await (const ev of streamPost(
        "/api/ask/stream",
        {
          question: text,
          stocks: stock ? [stock] : [],
          year_from: yearFrom ? Number(yearFrom) : null,
          year_to: yearTo ? Number(yearTo) : null,
          conversation_id: conversationId,
        },
        abort.current.signal,
      )) {
        if (ev.event === "meta") {
          const d = ev.data as { conversation_id: number; question: string; inherited: string[] };
          update(key, { resolved: d.question, inherited: d.inherited });
          if (d.conversation_id !== conversationId) {
            skipLoad.current = d.conversation_id;
            router.replace(`/?c=${d.conversation_id}`, { scroll: false });
          }
        } else if (ev.event === "sources") {
          update(key, { sources: (ev.data as { sources: Source[] }).sources });
        } else if (ev.event === "token") {
          answer += (ev.data as { text: string }).text;
          update(key, { answer });
        } else if (ev.event === "done") {
          const d = ev.data as AnswerDone;
          update(key, {
            answer: d.answer,
            sources: d.sources,
            warnings: d.warnings,
            unverified: d.unverified_numbers,
            trust: d.trust,
            found: d.found,
            refused: d.refused,
            cached: d.cached,
            messageId: d.message_id,
            model: d.model,
            elapsedMs: d.elapsed_ms,
            streaming: false,
          });
        } else if (ev.event === "error") {
          update(key, { error: (ev.data as { detail: string }).detail, streaming: false });
        }
      }
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) update(key, { error: "연결이 끊겼습니다", streaming: false });
    } finally {
      update(key, { streaming: false });
      setBusy(false);
      loadConversations();
    }
  }

  async function remove(id: number) {
    try {
      await del(`/api/conversations/${id}`);
      if (id === conversationId) router.replace("/");
      loadConversations();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[240px_1fr]">
      <aside className="order-2 lg:order-1" aria-label="대화 기록">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">대화 기록</h2>
          <button
            type="button"
            className="btn px-2 py-1 text-xs"
            onClick={() => {
              abort.current?.abort();
              skipLoad.current = null;
              router.replace("/");
            }}
          >
            새 대화
          </button>
        </div>
        {conversations.length ? (
          <ul className="space-y-1">
            {conversations.map((c) => (
              <li key={c.id} className="group flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => {
                    skipLoad.current = null;
                    router.replace(`/?c=${c.id}`);
                  }}
                  className={`min-w-0 flex-1 truncate rounded-lg px-2 py-1.5 text-left text-sm ${
                    c.id === conversationId ? "bg-accent-soft font-medium text-accent" : "hover:bg-surface-2"
                  }`}
                  aria-current={c.id === conversationId ? "true" : undefined}
                >
                  {c.title}
                </button>
                <button
                  type="button"
                  className="rounded px-1.5 text-xs text-muted opacity-60 hover:text-critical group-hover:opacity-100"
                  onClick={() => remove(c.id)}
                  aria-label={`"${c.title}" 대화 삭제`}
                >
                  삭제
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted">아직 대화가 없습니다.</p>
        )}
      </aside>

      <section className="order-1 min-w-0 lg:order-2">
        {turns.length === 0 ? (
          <div className="mb-6">
            <h1 className="text-xl font-bold sm:text-2xl">공시에 물어보세요</h1>
            <p className="mt-1 text-sm text-muted">
              사업보고서와 재무제표를 찾아 근거 번호와 함께 답합니다. 이어서 “그럼 전년은?”처럼 물어도 됩니다.
            </p>
            {examples.length ? (
              <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="예시로 물어보기">
                {examples.map((q) => (
                  <button key={q} type="button" className="btn text-left" onClick={() => ask(q)}>
                    {q}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="mb-6 space-y-6">
            {turns.map((t) => (
              <TurnView key={t.key} turn={t} />
            ))}
            <div ref={endRef} />
          </div>
        )}

        {error ? (
          <div className="mb-3">
            <ErrorBox error={error} />
          </div>
        ) : null}

        <form
          className="card sticky bottom-3 space-y-3 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            ask(question);
          }}
        >
          <label htmlFor="q" className="sr-only">
            질문
          </label>
          <textarea
            id="q"
            className="input min-h-16 resize-y"
            rows={2}
            maxLength={500}
            placeholder="예: 삼성전자 2024년 영업이익은?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                ask(question);
              }
            }}
          />
          <details>
            <summary className="cursor-pointer text-xs text-muted">검색 범위 정하기</summary>
            <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_120px_120px]">
              <CompanyInput value={company} onChange={setCompany} placeholder="회사 (선택)" />
              <input
                className="input"
                type="number"
                min={2000}
                max={2100}
                placeholder="시작 연도"
                aria-label="시작 연도"
                value={yearFrom}
                onChange={(e) => setYearFrom(e.target.value)}
              />
              <input
                className="input"
                type="number"
                min={2000}
                max={2100}
                placeholder="끝 연도"
                aria-label="끝 연도"
                value={yearTo}
                onChange={(e) => setYearTo(e.target.value)}
              />
            </div>
          </details>
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted">공시 정보 요약이며 투자 권유가 아닙니다.</p>
            {busy ? (
              <button type="button" className="btn" onClick={() => abort.current?.abort()}>
                멈추기
              </button>
            ) : (
              <button type="submit" className="btn btn-primary" disabled={question.trim().length < 2}>
                묻기
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
