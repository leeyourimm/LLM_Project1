import { type APIRequestContext, type Page, expect, test } from "@playwright/test";

// 계정 메일: 가입 이메일 인증, 비밀번호 재설정.
// 테스트용 백엔드는 메일을 보내지 않고 담아 두며, /api/_e2e/mail 로 마지막 메일을 보여 준다.
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8765}`;
const PASSWORD = "correct horse battery";
const NEW_PASSWORD = "new staple battery horse";

function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

// 메일은 응답 뒤에 보내므로 도착할 때까지 기다렸다가 본문의 링크를 꺼낸다
async function mailLink(request: APIRequestContext, to: string, subject: string, path: string): Promise<string> {
  let link = "";
  await expect
    .poll(async () => {
      const res = await request.get(`${API}/api/_e2e/mail`, { params: { to, subject } });
      const text = ((await res.json()) as { text?: string }).text ?? "";
      link = text.match(new RegExp(`https?://\\S+${path}\\?token=\\S+`))?.[0] ?? "";
      return link;
    })
    .not.toBe("");
  return link;
}

async function login(page: Page, email: string, password: string) {
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(password);
  await page.getByRole("button", { name: "로그인" }).click();
}

test("가입 인증 메일 → 비밀번호 재설정 메일 → 새 비밀번호로만 로그인", async ({ page, request }) => {
  const email = uniqueEmail("mail");

  await page.goto("/signup");
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(PASSWORD);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "가입하기" }).click();
  await expect(page.getByRole("heading", { name: "공시에 물어보세요" })).toBeVisible();

  // 가입하면 인증 메일이 오고, 링크를 열면 인증된다
  const verify = await mailLink(request, email, "이메일 인증", "/verify-email");
  await page.goto(verify);
  await expect(page.getByText("이메일을 인증했습니다")).toBeVisible();

  // 로그아웃 → 비밀번호를 잊었다고 요청
  await page.goto("/");
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByRole("link", { name: "비밀번호를 잊으셨나요?" }).click();
  // 화면이 바뀌기 전에 입력하면 로그인 화면의 칸에 들어가므로 재설정 화면을 기다린다
  await expect(page).toHaveURL(/\/forgot-password$/);
  await expect(page.getByRole("button", { name: "재설정 링크 받기" })).toBeVisible();
  await page.getByLabel("이메일").fill(email);
  await page.getByRole("button", { name: "재설정 링크 받기" }).click();
  await expect(page.getByRole("status")).toContainText("재설정 링크");

  // 메일의 링크를 열면 주소창에서 토큰이 사라지고 새 비밀번호를 정할 수 있다
  const reset = await mailLink(request, email, "비밀번호 재설정", "/reset-password");
  await page.goto(reset);
  await expect(page.getByRole("heading", { name: "새 비밀번호 정하기" })).toBeVisible();
  await expect(page).not.toHaveURL(/token=/);
  await page.getByLabel("새 비밀번호 (10자 이상)").fill(NEW_PASSWORD);
  await page.getByLabel("새 비밀번호 확인").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "비밀번호 바꾸기" }).click();
  await expect(page.getByRole("heading", { name: "공시에 물어보세요" })).toBeVisible();

  // 같은 링크는 두 번 쓸 수 없다
  await page.goto(reset);
  await page.getByLabel("새 비밀번호 (10자 이상)").fill(PASSWORD);
  await page.getByLabel("새 비밀번호 확인").fill(PASSWORD);
  await page.getByRole("button", { name: "비밀번호 바꾸기" }).click();
  await expect(page.getByRole("alert")).toBeVisible();

  // 예전 비밀번호로는 안 되고, 새 비밀번호로는 된다
  await page.goto("/");
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/login/);
  await login(page, email, PASSWORD);
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
  await login(page, email, NEW_PASSWORD);
  await expect(page.getByRole("heading", { name: "공시에 물어보세요" })).toBeVisible();
});

test("없는 계정으로 재설정을 요청해도 같은 안내가 나온다", async ({ page }) => {
  await page.goto("/forgot-password");
  await expect(page.getByRole("button", { name: "재설정 링크 받기" })).toBeVisible();
  await page.getByLabel("이메일").fill(uniqueEmail("nobody"));
  await page.getByRole("button", { name: "재설정 링크 받기" }).click();
  await expect(page.getByRole("status")).toContainText("가입된 계정이 있으면");
});
