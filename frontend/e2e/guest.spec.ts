import { type APIRequestContext, expect, test } from "@playwright/test";

// 가입 없이 체험하기 (ALLOW_GUEST=true): 로그인 화면의 버튼으로 바로 둘러보고, 예시 질문을 누르고,
// 알림처럼 가입이 필요한 기능은 안내를 보고, 체험을 끝내거나 가입해서 이어 쓴다.
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8765}`;
const PASSWORD = "correct horse battery";
const QUESTION = "삼성전자 2024년 DS 부문 매출은?";

async function forgottenCount(request: APIRequestContext): Promise<number> {
  const res = await request.get(`${API}/api/_e2e/forgotten`);
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { count: number }).count;
}

test("가입 없이 체험하기 → 예시 질문 → 막힌 기능 안내 → 체험 끝내기", async ({ page, request }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login\?next=%2F$/);
  await page.getByRole("button", { name: "가입 없이 체험하기" }).click();

  // 처음 화면으로 돌아와 체험 안내가 보인다
  await expect(page.getByRole("heading", { name: "공시에 물어보세요" })).toBeVisible();
  const banner = page.getByRole("status", { name: "체험 계정 안내" });
  await expect(banner).toContainText("가입 없이 체험하는 중입니다");
  await expect(banner).toContainText(/2[34]시간/);
  await expect(banner.getByRole("link", { name: "가입하고 이어서 쓰기" })).toHaveAttribute("href", "/signup");

  // 서버가 정한 예시 질문(EXAMPLE_QUESTIONS)을 누르면 바로 묻는다
  const examples = page.getByRole("group", { name: "예시로 물어보기" });
  await expect(examples.getByRole("button")).toHaveCount(3);
  await examples.getByRole("button").first().click();
  await expect(page.getByText("111조원입니다", { exact: false })).toBeVisible();
  await expect(page).toHaveURL(/\/\?c=\d+$/);

  // 알림은 체험 계정에서 쓸 수 없다고 알려 준다 (관심 종목 목록은 쓸 수 있다)
  await page.getByRole("link", { name: "관심 종목", exact: true }).click();
  await expect(page.getByText("체험 계정에서는 공시 알림", { exact: false })).toBeVisible();
  // API 도 같은 안내로 막는다
  const blocked = await page.request.get("/api/alerts");
  expect(blocked.status()).toBe(403);
  expect(((await blocked.json()) as { detail: string }).detail).toContain("가입하면 쓸 수 있습니다");

  // 계정 화면: 체험 계정 안내
  await page.getByRole("link", { name: "체험 계정", exact: true }).click();
  await expect(page.getByRole("heading", { name: "체험 계정", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "내 데이터 내려받기" })).toHaveCount(0);

  // 체험 끝내기: 기록과 계정을 바로 지우고 LLM 추적 삭제도 요청한다
  const before = await forgottenCount(request);
  await page.getByRole("button", { name: "체험 끝내기", exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("button", { name: "가입 없이 체험하기" })).toBeVisible();
  await expect.poll(() => forgottenCount(request)).toBe(before + 1);
});

test("체험하다 가입하면 대화 기록이 그대로 이어진다", async ({ page }) => {
  const email = `e2e-guest-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/login");
  await page.getByRole("button", { name: "가입 없이 체험하기" }).click();
  await page.getByLabel("질문").fill(QUESTION);
  await page.getByRole("button", { name: "묻기" }).click();
  await expect(page.getByText("111조원입니다", { exact: false })).toBeVisible();

  await page.getByRole("link", { name: "가입하고 이어서 쓰기" }).click();
  await expect(page.getByText("체험하며 남긴 대화 기록과 관심 종목이 그대로 이어집니다")).toBeVisible();
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(PASSWORD);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "가입하기" }).click();

  await expect(page.getByRole("link", { name: email })).toBeVisible();
  await expect(page.getByRole("status", { name: "체험 계정 안내" })).toHaveCount(0);
  const history = page.getByRole("complementary", { name: "대화 기록" });
  await expect(history.getByRole("button", { name: QUESTION, exact: true })).toBeVisible();
});
