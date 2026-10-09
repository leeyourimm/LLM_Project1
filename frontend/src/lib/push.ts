// 웹 푸시 구독 (브라우저 쪽). 서비스 워커는 public/sw.js, 서버 API 는 /api/alerts/push.
// 서버 공개키(VAPID)는 /api/alerts 가 알려 준다. 비밀키는 서버에만 있다.

import { del, post } from "@/lib/api";

export const SW_URL = "/sw.js";

/** 이 브라우저가 웹 푸시를 쓸 수 있는지 (https 또는 localhost 에서만 된다). */
export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** base64url 문자열 → 바이트 (applicationServerKey 용). */
export function keyToBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** 이 구독이 지금 서버 키로 만든 것인지. 서버 키가 바뀌었으면 다시 구독해야 한다. */
export function sameKey(current: ArrayBuffer | null | undefined, expected: Uint8Array): boolean {
  if (!current) return false;
  const a = new Uint8Array(current);
  return a.length === expected.length && a.every((v, i) => v === expected[i]);
}

/** 구독 주소의 SHA-256 (hex). 서버는 주소 대신 이 값을 돌려줘서 "이 브라우저"를 알아본다. */
export async function endpointKey(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? reg.pushManager.getSubscription() : null;
}

/** 알림 권한을 묻고 구독한 뒤 서버에 등록한다. 버튼을 누를 때만 부른다 (권한 요청 규칙). */
export async function subscribe(publicKey: string): Promise<void> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("브라우저에서 알림을 허용해야 받을 수 있습니다. 주소창 옆 사이트 설정에서 알림을 허용해 주세요.");
  }
  await navigator.serviceWorker.register(SW_URL, { scope: "/" });
  const reg = await navigator.serviceWorker.ready;
  const key = keyToBytes(publicKey);
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options.applicationServerKey, key)) {
    // 서버 키가 바뀌었다: 예전 구독은 서버와 브라우저 모두에서 지우고 새로 만든다
    await del("/api/alerts/push/device", { endpoint: sub.endpoint }).catch(() => undefined);
    await sub.unsubscribe();
    sub = null;
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await post("/api/alerts/push", sub.toJSON());
}

/** 이 브라우저의 구독을 서버와 브라우저에서 모두 지운다. */
export async function unsubscribe(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await del("/api/alerts/push/device", { endpoint: sub.endpoint });
  await sub.unsubscribe();
}
