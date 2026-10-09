"use client";

import { useCallback, useEffect, useState } from "react";
import { ErrorBox } from "@/components/ui";
import { api, del, patch, post } from "@/lib/api";
import type { AlertChannel, AlertSettings as Settings } from "@/lib/types";

const LABEL = { email: "이메일", telegram: "텔레그램" } as const;

function stateOf(kind: "email" | "telegram", s: Settings, ch?: AlertChannel) {
  if (!s.available[kind]) return "서버에 설정되지 않음";
  if (!ch) return "연결 안 됨";
  if (!ch.verified) return ch.pending ? "인증 대기 중" : "인증 만료";
  return ch.enabled ? "켜짐" : "꺼짐";
}

export function AlertSettings() {
  const [s, setS] = useState<Settings | null>(null);
  const [note, setNote] = useState<React.ReactNode>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(() => {
    api<Settings>("/api/alerts").then(setS).catch(setError);
  }, []);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<void>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      load();
    }
  };

  if (!s) return null;
  if (!s.per_user) {
    return (
      <div className="card text-sm text-muted">
        혼자 쓰는 설정입니다. 알림은 서버 <code>.env</code>의 <code>ALERT_WEBHOOK_URL</code>,{" "}
        <code>ALERT_TELEGRAM_CHAT_ID</code>, <code>ALERT_EMAIL_TO</code>로 받습니다.
      </div>
    );
  }
  const byKind = Object.fromEntries(s.channels.map((c) => [c.kind, c])) as Record<string, AlertChannel | undefined>;
  return (
    <section className="card">
      <h2 className="mb-2 font-semibold">알림 받을 곳</h2>
      <ul className="divide-y divide-line">
        {(["email", "telegram"] as const).map((kind) => {
          const ch = byKind[kind];
          return (
            <li key={kind} className="flex flex-wrap items-center gap-2 py-2.5 text-sm">
              <span className="font-medium">{LABEL[kind]}</span>
              <span className="text-muted">· {stateOf(kind, s, ch)}</span>
              <span className="flex-1" />
              {s.available[kind] && (!ch || !ch.verified) ? (
                <button
                  type="button"
                  className="btn"
                  onClick={() =>
                    run(async () => {
                      if (kind === "email") {
                        const r = await post<{ sent_to: string }>("/api/alerts/email");
                        setNote(`${r.sent_to}로 인증 메일을 보냈습니다. 메일의 링크를 열면 알림이 시작됩니다.`);
                      } else {
                        const r = await post<{ link: string }>("/api/alerts/telegram");
                        setNote(
                          <>
                            30분 안에{" "}
                            <a className="link" href={r.link} target="_blank" rel="noopener noreferrer">
                              봇 열기
                            </a>
                            를 누르고 텔레그램에서 시작을 누르세요.
                          </>,
                        );
                      }
                    })
                  }
                >
                  {kind === "email" ? "인증 메일 받기" : "텔레그램 연결"}
                </button>
              ) : null}
              {ch?.verified ? (
                <>
                  <button type="button" className="btn" onClick={() => run(() => patch(`/api/alerts/${kind}`, { enabled: !ch.enabled }))}>
                    {ch.enabled ? "끄기" : "켜기"}
                  </button>
                  <button type="button" className="btn" onClick={() => run(() => del(`/api/alerts/${kind}`))}>
                    삭제
                  </button>
                </>
              ) : null}
            </li>
          );
        })}
      </ul>
      {note ? (
        <p role="status" className="mt-2 text-sm">
          {note}
        </p>
      ) : null}
      {error ? (
        <div className="mt-2">
          <ErrorBox error={error} />
        </div>
      ) : null}
    </section>
  );
}
