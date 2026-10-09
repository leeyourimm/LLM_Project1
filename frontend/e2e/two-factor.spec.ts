import { createHmac } from "node:crypto";
import { type Page, expect, test } from "@playwright/test";

// 2단계 인증(TOTP): 계정 화면에서 켜기 → 로그아웃 → 로그인할 때 코드 요구 → 복구 코드로도 로그인 → 끄기.
// 인증 앱 대신 화면에 보이는 직접 입력 키로 여기서 코드를 계산한다 (RFC 6238, SHA-1, 30초, 6자리).
const PASSWORD = "correct horse battery";

function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

function base32(text: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let value = 0;
  const out: number[] = [];
  for (const c of text.replace(/[\s=]/g, "").toUpperCase()) {
    const i = alphabet.indexOf(c);
    if (i < 0) throw new Error(`base32 아님: ${c}`);
    value = (value << 5) | i;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      out.push((value >>> bits) & 0xff);
      value &= (1 << bits) - 1;
    }
  }
  return Buffer.from(out);
}

function totp(secret: string, at = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const h = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = h[h.length - 1] & 0xf;
  return String((h.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

// 맞는 코드와 한 자리만 다른 코드
function wrong(code: string): string {
  return code.slice(0, 5) + String((Number(code[5]) + 1) % 10);
}

async function login(page: Page, email: string) {
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(PASSWORD);
  await page.getByRole("button", { name: "로그인" }).click();
}

async function logout(page: Page) {
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/login/);
}

test("2단계 인증 켜기 → 로그인에 코드 필요 → 복구 코드 → 끄기", async ({ page }) => {
  test.setTimeout(60_000);
  const email = uniqueEmail("2fa");

  await page.goto("/signup");
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill(PASSWORD);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "가입하기" }).click();
  await expect(page.getByRole("link", { name: email })).toBeVisible();

  // 계정 화면: 비밀번호를 넣으면 QR 코드와 직접 입력 키가 나온다
  await page.goto("/account");
  const card = page.getByRole("region", { name: "2단계 인증" });
  await card.locator("#tf-setup-password").fill("wrong password!!");
  await card.getByRole("button", { name: "2단계 인증 켜기" }).click();
  await expect(card.getByText("비밀번호가 맞지 않습니다")).toBeVisible();
  await card.locator("#tf-setup-password").fill(PASSWORD);
  await card.getByRole("button", { name: "2단계 인증 켜기" }).click();
  const qr = card.getByRole("img", { name: "인증 앱 등록용 QR 코드" });
  await expect(qr).toBeVisible();
  expect(await qr.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
  const secret = ((await card.locator("#tf-secret").textContent()) ?? "").replace(/\s/g, "");
  expect(secret).toMatch(/^[A-Z2-7]{32}$/);
  const uri = await card.getByRole("link", { name: "인증 앱에서 바로 열기" }).getAttribute("href");
  expect(uri).toMatch(/^otpauth:\/\/totp\//);
  expect(new URL(uri ?? "").searchParams.get("secret")).toBe(secret);

  // 틀린 코드로는 켜지지 않는다
  await card.locator("#tf-enable-code").fill(wrong(totp(secret)));
  await card.getByRole("button", { name: "확인하고 켜기" }).click();
  await expect(card.getByText(/인증 코드가 맞지 않습니다/)).toBeVisible();
  await card.locator("#tf-enable-code").fill(totp(secret));
  await card.getByRole("button", { name: "확인하고 켜기" }).click();

  // 복구 코드 10개를 한 번만 보여 준다. 저장했다고 표시해야 닫힌다
  await expect(card.getByText("2단계 인증을 켰습니다")).toBeVisible();
  const codes = (await card.getByRole("list", { name: "복구 코드" }).getByRole("listitem").allTextContents()).map((c) => c.trim());
  expect(codes).toHaveLength(10);
  for (const c of codes) expect(c).toMatch(/^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$/);
  const done = card.getByRole("button", { name: "완료" });
  await expect(done).toBeDisabled();
  await card.getByRole("checkbox", { name: "복구 코드를 안전한 곳에 저장했습니다." }).check();
  await done.click();
  await expect(card.getByText("남은 복구 코드 10개")).toBeVisible();

  // 내 데이터에는 켰는지만 있고 키·복구 코드는 없다
  const exported = await (await page.request.get("/api/account/export")).json();
  expect(exported.two_factor).toMatchObject({ enabled: true, recovery_codes_left: 10 });
  const text = JSON.stringify(exported);
  expect(text).not.toContain(secret);
  for (const c of codes) expect(text).not.toContain(c);

  // 로그아웃 → 비밀번호만으로는 로그인되지 않는다
  await logout(page);
  await login(page, email);
  await expect(page.getByRole("heading", { name: "2단계 인증" })).toBeVisible();
  expect((await (await page.request.get("/api/auth/me")).json()).user).toBeNull();

  // 틀린 코드 → 오류, 맞는 코드 → 로그인. 켤 때 쓴 시간 구간은 다시 쓸 수 없어 다음 구간 코드를 쓴다
  const next = Date.now() + 30_000;
  await page.getByLabel("인증 코드").fill(wrong(totp(secret, next)));
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByText("인증 코드가 맞지 않습니다")).toBeVisible();
  await page.getByLabel("인증 코드").fill(totp(secret, next));
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page).toHaveURL(/\/account$/);
  await expect(page.getByRole("link", { name: email })).toBeVisible();

  // 복구 코드로 로그인: 한 번 쓴 코드는 다시 쓸 수 없다
  await logout(page);
  await login(page, email);
  await page.getByRole("button", { name: "복구 코드 쓰기" }).click();
  await page.getByLabel("복구 코드").fill(codes[0].toUpperCase());
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByText("남은 복구 코드는 9개입니다")).toBeVisible();
  await page.getByRole("button", { name: "계속" }).click();
  await expect(page).toHaveURL(/\/account$/);

  await logout(page);
  await login(page, email);
  await page.getByRole("button", { name: "복구 코드 쓰기" }).click();
  await page.getByLabel("복구 코드").fill(codes[0]);
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByText("인증 코드가 맞지 않습니다")).toBeVisible();
  await page.getByLabel("복구 코드").fill(codes[1]);
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByText("남은 복구 코드는 8개입니다")).toBeVisible();
  await page.getByRole("button", { name: "계속" }).click();
  await expect(page).toHaveURL(/\/account$/);

  // 끄기: 비밀번호와 코드가 모두 필요하다
  const settings = page.getByRole("region", { name: "2단계 인증" });
  await expect(settings.getByText("남은 복구 코드 8개")).toBeVisible();
  await settings.locator("#tf-password").fill(PASSWORD);
  await settings.locator("#tf-code").fill(codes[2]);
  await settings.getByRole("button", { name: "2단계 인증 끄기" }).click();
  await expect(settings.getByRole("button", { name: "2단계 인증 켜기" })).toBeVisible();

  // 끈 뒤에는 비밀번호만으로 로그인된다
  await logout(page);
  await login(page, email);
  await expect(page).toHaveURL(/\/account$/);
  await expect(page.getByRole("link", { name: email })).toBeVisible();
});
