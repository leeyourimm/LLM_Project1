// 웹 푸시 알림용 서비스 워커. 알림 설정 화면에서 "이 브라우저에서 받기"를 누를 때 등록한다.
// 알림을 보여 주고 누르면 해당 화면을 여는 일만 한다. 화면 요청(fetch)은 가로채지 않고 캐시도 하지 않는다.
//
// 알림 내용은 서버가 이 브라우저의 키로 암호화해 보내고(RFC 8291), 브라우저가 풀어서 여기로 넘긴다.
// 내용: { title, body, url } — 짧은 요약과 열 주소뿐이며 비밀값은 들어 있지 않다.

const FALLBACK = "/feed";
// 알림을 누르면 열 수 있는 곳: 이 사이트, DART 원문
const ALLOWED_ORIGINS = ["https://dart.fss.or.kr"];

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function safeUrl(value) {
  try {
    const url = new URL(typeof value === "string" && value ? value : FALLBACK, self.location.origin);
    if (url.origin === self.location.origin || ALLOWED_ORIGINS.includes(url.origin)) return url.href;
  } catch {
    // 아래 기본 화면으로
  }
  return new URL(FALLBACK, self.location.origin).href;
}

function text(value, max) {
  return typeof value === "string" ? value.slice(0, max) : "";
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = text(data.title, 120) || "DART 공시 알림";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: text(data.body, 500),
      data: { url: safeUrl(data.url) },
      lang: "ko",
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = safeUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (client.url === url && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(url);
    })(),
  );
});

// 브라우저가 구독을 새로 바꾸면(만료 등) 새 구독을 서버에 다시 알린다.
// 로그인이 풀려 있으면 실패하고, 예전 구독은 서버가 보낼 때 410 을 받아 지운다.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      const key = event.oldSubscription && event.oldSubscription.options.applicationServerKey;
      const sub =
        event.newSubscription ||
        (key && (await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key })));
      if (!sub) return;
      await fetch("/api/alerts/push", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(sub.toJSON()),
      });
    })().catch(() => undefined),
  );
});
