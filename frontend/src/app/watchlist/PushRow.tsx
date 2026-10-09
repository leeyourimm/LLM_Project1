"use client";

import { useEffect, useState } from "react";
import { del, patch, post } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { currentSubscription, endpointKey, pushSupported, subscribe, unsubscribe } from "@/lib/push";
import type { AlertChannel, AlertSettings } from "@/lib/types";

// 알림 설정의 "웹 푸시" 줄: 이 브라우저 구독·해제, 시험 알림, 다른 브라우저 목록
export function PushRow({
  s,
  ch,
  run,
  setNote,
}: {
  s: AlertSettings;
  ch?: AlertChannel;
  run: (fn: () => Promise<void>) => Promise<void>;
  setNote: (note: React.ReactNode) => void;
}) {
  const [supported, setSupported] = useState<boolean | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | null>(null);
  // 이 브라우저 구독 주소의 해시. undefined 면 아직 확인 중
  const [thisKey, setThisKey] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    const ok = pushSupported();
    setSupported(ok);
    if (!ok) {
      setThisKey(null);
      return;
    }
    setPermission(Notification.permission);
    currentSubscription()
      .then((sub) => (sub ? endpointKey(sub.endpoint) : null))
      .then(setThisKey)
      .catch(() => setThisKey(null));
  }, [s]);

  const devices = s.push_devices ?? [];
  const available = Boolean(s.available.push && s.push_public_key);
  const here = thisKey ? devices.find((d) => d.key === thisKey) : undefined;
  const state = !available
    ? "서버에 설정되지 않음"
    : !ch
      ? "연결 안 됨"
      : ch.enabled
        ? `켜짐 · 브라우저 ${devices.length}개`
        : "꺼짐";

  return (
    <li className="py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">웹 푸시</span>
        <span className="text-muted">· {state}</span>
        <span className="flex-1" />
        {available && supported && thisKey !== undefined && !here && permission !== "denied" ? (
          <button
            type="button"
            className="btn"
            onClick={() =>
              run(async () => {
                await subscribe(s.push_public_key as string);
                setNote("이 브라우저로 공시 알림을 보냅니다. 시험 알림으로 확인해 보세요.");
              })
            }
          >
            이 브라우저에서 받기
          </button>
        ) : null}
        {here ? (
          <>
            <button
              type="button"
              className="btn"
              onClick={() =>
                run(async () => {
                  const r = await post<{ sent: number }>("/api/alerts/push/test");
                  setNote(`시험 알림을 브라우저 ${r.sent}곳에 보냈습니다.`);
                })
              }
            >
              시험 알림
            </button>
            <button
              type="button"
              className="btn"
              onClick={() =>
                run(async () => {
                  await unsubscribe();
                  setNote("이 브라우저의 웹 푸시를 해제했습니다.");
                })
              }
            >
              이 브라우저 해제
            </button>
          </>
        ) : null}
        {ch ? (
          <>
            <button type="button" className="btn" onClick={() => run(() => patch("/api/alerts/push", { enabled: !ch.enabled }))}>
              {ch.enabled ? "끄기" : "켜기"}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() =>
                run(async () => {
                  await del("/api/alerts/push");
                  await currentSubscription()
                    .then((sub) => sub?.unsubscribe())
                    .catch(() => undefined);
                })
              }
            >
              삭제
            </button>
          </>
        ) : null}
      </div>
      {available && supported === false ? (
        <p className="mt-1 text-xs text-muted">
          이 브라우저는 웹 푸시를 지원하지 않습니다. 아이폰·아이패드는 Safari 공유 메뉴의 &ldquo;홈 화면에 추가&rdquo;로 연 화면에서 받을 수
          있습니다.
        </p>
      ) : null}
      {available && supported && permission === "denied" && !here ? (
        <p className="mt-1 text-xs text-muted">브라우저에서 이 사이트의 알림이 차단되어 있습니다. 주소창 옆 사이트 설정에서 알림을 허용해 주세요.</p>
      ) : null}
      {here ? <p className="mt-1 text-xs text-muted">로그아웃해도 이 브라우저로 알림이 옵니다. 공용 컴퓨터라면 해제하세요.</p> : null}
      {devices.length ? (
        <ul className="mt-2 space-y-1" aria-label="웹 푸시를 받는 브라우저">
          {devices.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <span className="text-ink">{d.label || "브라우저"}</span>
              {d === here ? <span className="rounded-full bg-accent-soft px-2 py-0.5 text-ink">이 브라우저</span> : null}
              <span>
                · {shortDate(d.created_at)} 등록{d.last_sent_at ? ` · 마지막 알림 ${shortDate(d.last_sent_at)}` : ""}
              </span>
              <span className="flex-1" />
              {d === here ? null : (
                <button type="button" className="btn px-2 py-1 text-xs" onClick={() => run(() => del(`/api/alerts/push/devices/${d.id}`))}>
                  해제
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
