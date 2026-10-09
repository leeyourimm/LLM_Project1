import { readFile } from "node:fs/promises";
import { type APIRequestContext, type Page, expect, test } from "@playwright/test";

// 백엔드에 직접 붙는 주소 (화면은 Next 를 거쳐 /api 로 간다)
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8765}`;
const PASSWORD = "correct horse battery";
const QUESTION = "삼성전자 2024년 DS 부문 매출은?";

function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function login(page: Page, email: string, password = PASSWORD) {
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(password);
  await page.getByRole("button", { name: "로그인" }).click();
}

async function forgottenCount(request: APIRequestContext): Promise<number> {
  const res = await request.get(`${API}/api/_e2e/forgotten`);
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { count: number }).count;
}

test("가입 → 로그인 → 질문 → 내 데이터 내려받기 → 탈퇴 → 다시 로그인할 수 없음", async ({ page, request }) => {
  const email = uniqueEmail("flow");

  // 가입: 약관 동의 없이는 보낼 수 없다
  await page.goto("/signup");
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(PASSWORD);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "가입하기" }).click();
  await expect(page.getByRole("heading", { name: "공시에 물어보세요" })).toBeVisible();
  await expect(page.getByRole("link", { name: email })).toBeVisible();

  // 로그아웃하면 로그인 화면으로, 다시 로그인하면 처음 화면으로
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/login\?next=%2F$/);
  await login(page, email);
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("link", { name: email })).toBeVisible();

  // 질문 (가짜 언어 모델이 스트리밍으로 답한다)
  await page.getByLabel("질문").fill(QUESTION);
  await page.getByRole("button", { name: "묻기" }).click();
  await expect(page.getByText("111조원입니다", { exact: false })).toBeVisible();
  await expect(page).toHaveURL(/\/\?c=\d+$/);
  await expect(page.getByRole("complementary", { name: "대화 기록" }).getByRole("button", { name: QUESTION, exact: true })).toBeVisible();

  // 계정 화면에서 내 데이터 내려받기
  await page.getByRole("link", { name: email }).click();
  await expect(page.getByRole("heading", { name: "계정", exact: true })).toBeVisible();
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "내 데이터 내려받기" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^dartrag-export-\d{8}\.json$/);
  const exported = JSON.parse(await readFile(await download.path(), "utf8"));
  expect(exported.account.email).toBe(email);
  expect(JSON.stringify(exported)).not.toContain("password");
  const messages = exported.conversations.flatMap((c: { messages: { role: string; content: string }[] }) => c.messages);
  expect(messages.map((m: { content: string }) => m.content)).toContain(QUESTION);
  expect(messages.some((m: { role: string; content: string }) => m.role === "assistant" && m.content.includes("111조원"))).toBe(true);

  // 탈퇴: 비밀번호와 확인 표시가 있어야 버튼이 켜진다
  const before = await forgottenCount(request);
  const remove = page.getByRole("button", { name: "계정 삭제" });
  await expect(remove).toBeDisabled();
  await page.locator("#delete-password").fill(PASSWORD);
  await page.getByRole("checkbox", { name: /되돌릴 수 없다는 것을 이해했습니다/ }).check();
  await remove.click();
  // 로그인 화면으로 (지금 화면이 ?next 로 붙을 수 있다)
  await expect(page).toHaveURL(/\/login(\?next=%2Faccount)?$/);
  await expect(page.getByRole("button", { name: "로그인" })).toBeVisible();
  // 탈퇴 응답 뒤 LLM 추적 삭제를 요청했다
  await expect.poll(() => forgottenCount(request)).toBe(before + 1);

  // 같은 이메일·비밀번호로 다시 로그인할 수 없다
  await login(page, email);
  await expect(page.getByText("이메일 또는 비밀번호가 맞지 않습니다")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
  const me = await page.request.get("/api/auth/me");
  expect((await me.json()).user).toBeNull();
});

test.describe("로그인 뒤 돌아갈 주소(next)는 이 사이트 안만", () => {
  let email: string;

  test.beforeAll(async ({ request }) => {
    email = uniqueEmail("next");
    // 화면을 거치지 않고 백엔드에 바로 가입 (이 요청의 로그인 쿠키는 브라우저와 따로다)
    const res = await request.post(`${API}/api/auth/signup`, { data: { email, password: PASSWORD } });
    expect(res.ok()).toBeTruthy();
  });

  for (const next of ["//evil.example/x", "/\\evil.example/x", "https://evil.example/x", "javascript:alert(1)"]) {
    test(`next=${next} 이면 처음 화면으로`, async ({ page }) => {
      await page.goto(`/login?next=${encodeURIComponent(next)}`);
      await login(page, email);
      await expect(page.getByRole("heading", { name: "공시에 물어보세요" })).toBeVisible();
      expect(page.url()).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/$/);
    });
  }

  test("이 사이트 안의 경로면 그 화면으로", async ({ page }) => {
    await page.goto(`/login?next=${encodeURIComponent("/watchlist")}`);
    await login(page, email);
    await expect(page).toHaveURL(/\/watchlist$/);
  });

  test("로그인이 필요한 화면은 로그인 화면으로 보내고 돌아온다", async ({ page }) => {
    await page.goto("/account");
    await expect(page).toHaveURL(/\/login\?next=%2Faccount$/);
    await login(page, email);
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByRole("heading", { name: "계정", exact: true })).toBeVisible();
  });
});
