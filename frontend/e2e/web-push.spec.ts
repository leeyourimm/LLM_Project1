import { createECDH, randomBytes } from "node:crypto";
import { type APIRequestContext, expect, test } from "@playwright/test";

// 웹 푸시 알림 설정: 이 브라우저 구독 → 시험 알림 → 해제.
// 테스트 브라우저는 실제 푸시 서비스(FCM 등)에 붙을 수 없으므로 브라우저의 구독 API(PushManager)만 가짜로 바꾸고,
// 테스트용 백엔드는 알림을 보내는 대신 담아 두었다가 /api/_e2e/push 로 보여 준다.
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8765}`;
const PASSWORD = "correct horse battery";

function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function pushed(request: APIRequestContext): Promise<{ count: number; last: Record<string, string> | null }> {
  const res = await request.get(`${API}/api/_e2e/push`);
  expect(res.ok()).toBeTruthy();
  return res.json();
}

test("알림 설정에서 이 브라우저로 웹 푸시 받기 → 시험 알림 → 해제", async ({ page, request }) => {
  const email = uniqueEmail("push");
  // 브라우저가 만드는 구독 키: 실제 P-256 공개키와 16바이트 인증값 (서버가 형식을 검사한다)
  const ecdh = createECDH("prime256v1");
  const keys = { p256dh: ecdh.generateKeys().toString("base64url"), auth: randomBytes(16).toString("base64url") };
  const endpoint = `https://fcm.googleapis.com/fcm/send/e2e-${randomBytes(12).toString("hex")}`;

  await page.addInitScript(
    ({ endpoint, keys }) => {
      let current: unknown = null;
      const pushManager = {
        getSubscription: async () => current,
        subscribe: async (options: { applicationServerKey: Uint8Array }) => {
          const sub = {
            endpoint,
            options: { applicationServerKey: options.applicationServerKey.slice().buffer },
            toJSON: () => ({ endpoint, expirationTime: null, keys }),
            unsubscribe: async () => {
              current = null;
              return true;
            },
          };
          current = sub;
          return sub;
        },
      };
      const registration = { scope: `${location.origin}/`, pushManager };
      Object.defineProperty(navigator, "serviceWorker", {
        configurable: true,
        value: {
          register: async () => registration,
          ready: Promise.resolve(registration),
          getRegistration: async () => registration,
        },
      });
      Object.defineProperty(window, "PushManager", { configurable: true, value: function PushManager() {} });
      Object.defineProperty(Notification, "permission", { configurable: true, get: () => "granted" });
      Notification.requestPermission = async () => "granted" as NotificationPermission;
    },
    { endpoint, keys },
  );

  await page.goto("/signup");
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(PASSWORD);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "가입하기" }).click();
  await expect(page.getByRole("link", { name: email })).toBeVisible();

  await page.goto("/watchlist");
  const row = page.getByRole("listitem").filter({ has: page.getByText("웹 푸시", { exact: true }) });
  await expect(row.getByText("· 연결 안 됨")).toBeVisible();
  await row.getByRole("button", { name: "이 브라우저에서 받기" }).click();
  await expect(row.getByText("· 켜짐 · 브라우저 1개")).toBeVisible();
  const devices = page.getByRole("list", { name: "웹 푸시를 받는 브라우저" });
  await expect(devices.getByText("이 브라우저", { exact: true })).toBeVisible();
  await expect(page.getByText("로그아웃해도 이 브라우저로 알림이 옵니다")).toBeVisible();

  // 시험 알림: 짧은 제목·내용·주소만 보낸다
  const before = (await pushed(request)).count;
  await page.getByRole("button", { name: "시험 알림" }).click();
  await expect(page.getByText("시험 알림을 브라우저 1곳에 보냈습니다.")).toBeVisible();
  const after = await pushed(request);
  expect(after.count).toBe(before + 1);
  expect(after.last).toEqual({
    push_service: "fcm.googleapis.com",
    title: "DART 공시 알림 시험",
    body: "이 브라우저로 공시 알림이 옵니다.",
    url: "/watchlist",
  });

  // 화면이 받은 설정에는 구독 주소와 키가 없다 (주소의 해시만)
  const settings = await (await page.request.get("/api/alerts")).json();
  const text = JSON.stringify(settings);
  expect(text).not.toContain(endpoint);
  expect(text).not.toContain(keys.p256dh);
  expect(text).not.toContain(keys.auth);

  // 해제하면 구독이 지워지고 다시 받을 수 있는 상태로
  await page.getByRole("button", { name: "이 브라우저 해제" }).click();
  await expect(page.getByText("이 브라우저의 웹 푸시를 해제했습니다.")).toBeVisible();
  await expect(row.getByRole("button", { name: "이 브라우저에서 받기" })).toBeVisible();
  await expect(row.getByText("· 연결 안 됨")).toBeVisible();
  await expect(devices).toHaveCount(0);
});
