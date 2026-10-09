import { type Page, expect, test } from "@playwright/test";

// 답변 화면: [n] 을 누르면 근거 원문 패널이 그 출처를 열고 답변이 인용한 문단을 표시한다.
// 테스트용 백엔드(tests/e2e_server.py)는 앞뒤 문단을 붙인 출처 하나와 답변에 쓰지 않은 출처 하나를 돌려주고,
// 인용한 사업보고서 뒤에 반기보고서가 접수된 것으로 넣어 둔다.
const PASSWORD = "correct horse battery";
const QUESTION = "삼성전자 2024년 DS 부문 매출은?";
const CITED = "DS 부문 매출은 111조원이다.";
const AFTER = "메모리 반도체 수요 회복";

async function signUpAndAsk(page: Page) {
  const email = `e2e-cite-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  // 브라우저와 같은 쿠키를 쓰는 요청으로 가입 (가입 화면은 account.spec 이 확인한다)
  const res = await page.request.post("/api/auth/signup", { data: { email, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  await page.goto("/");
  await page.getByLabel("질문").fill(QUESTION);
  await page.getByRole("button", { name: "묻기" }).click();
  await expect(page.getByText("111조원입니다", { exact: false })).toBeVisible();
  // 답변이 끝나야 근거 점검이 나온다
  await expect(page.getByTestId("trust")).toBeVisible();
}

test("답변의 [1] 을 누르면 근거 원문이 열리고 인용한 문단이 표시된다", async ({ page }) => {
  await signUpAndAsk(page);
  await expect(page.getByText("인용한 출처 1개")).toBeVisible();
  await expect(page.getByText("검색했지만 답변에 쓰지 않은 문서 1개는 숨겼습니다.")).toBeVisible();

  // 근거 점검: 근거 수, 더 최근 공시, 숫자 검증 (점수가 아니라 사실)
  const trust = page.getByTestId("trust");
  await expect(trust).toContainText("출처 1개 · 공시 1건");
  await expect(trust).toContainText("더 최근 공시 있음");
  await expect(trust).toContainText("숫자 1개 원문과 일치");
  // 더 최근 공시가 있다는 것만으로는 접혀 있고, "자세히"를 누르면 문장으로 풀어 쓴다
  const more = trust.getByRole("button", { name: "자세히" });
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await more.click();
  await expect(trust.getByRole("button", { name: "접기" })).toHaveAttribute("aria-expanded", "true");
  await expect(trust).toContainText("「반기보고서 (2025.06)」(2025-08-14 접수)가 나왔습니다");
  await expect(trust).toContainText("이 출처는 공시 1건에서 나왔습니다");
  await expect(trust).not.toContainText("%");

  // 큰 화면에서는 근거 원문 패널이 답변 옆에 있다
  const answerCard = page.locator("article .card").first();
  const panelTitle = page.getByRole("heading", { name: /근거 원문/ });
  const [answerBox, panelBox] = [await answerCard.boundingBox(), await panelTitle.boundingBox()];
  expect(panelBox!.x).toBeGreaterThanOrEqual(answerBox!.x + answerBox!.width);

  const cite = page.getByRole("button", { name: "출처 1 원문 보기" });
  await expect(cite).toHaveAttribute("aria-expanded", "false");
  await cite.click();
  await expect(cite).toHaveAttribute("aria-expanded", "true");

  const passage = page.getByRole("region", { name: /출처 1 원문/ });
  await expect(passage).toBeFocused();
  const mark = passage.getByTestId("cited-passage");
  await expect(mark).toContainText(CITED);
  await expect(mark).toBeInViewport();
  // 답변에 옮긴 숫자를 원문에서 따로 표시
  await expect(mark.locator("strong")).toHaveText("111조원");
  // 모델이 함께 읽은 앞뒤 문단도 보이지만 인용 문단 밖이다
  await expect(passage).toContainText(AFTER);
  await expect(mark).not.toContainText(AFTER);
  // DART 원문 링크는 그대로
  await expect(page.getByRole("link", { name: /DART 원문 열기/ })).toHaveAttribute("href", /rcpNo=20250311000001$/);

  // 답변으로 돌아가면 눌렀던 [1] 에 포커스
  await page.getByRole("button", { name: "답변으로 돌아가기" }).click();
  await expect(cite).toBeFocused();

  // 출처 제목을 다시 누르면 닫힌다
  await page.getByRole("button", { name: /사업보고서 \(2024\.12\) · II\. 사업의 내용/ }).click();
  await expect(passage).toBeHidden();
  await expect(cite).toHaveAttribute("aria-expanded", "false");
});

test("키보드만으로 [1] 을 열고 Esc 로 답변에 돌아온다", async ({ page }) => {
  await signUpAndAsk(page);
  const cite = page.getByRole("button", { name: "출처 1 원문 보기" });
  await cite.focus();
  await page.keyboard.press("Enter");
  const passage = page.getByRole("region", { name: /출처 1 원문/ });
  await expect(passage).toBeFocused();
  await expect(passage.getByTestId("cited-passage")).toContainText(CITED);
  await page.keyboard.press("Escape");
  await expect(passage).toBeHidden();
  await expect(cite).toBeFocused();
});

test.describe("휴대폰 화면, 어두운 화면", () => {
  test.use({ viewport: { width: 390, height: 844 }, colorScheme: "dark", hasTouch: true });

  test("인용 문단이 화면 안에 들어오고 어두운 색으로 표시된다", async ({ page }) => {
    await signUpAndAsk(page);
    await page.getByRole("button", { name: "출처 1 원문 보기" }).tap();
    const mark = page.getByTestId("cited-passage");
    await expect(mark).toBeInViewport();
    // 아래에 붙어 있는 질문 입력창에 가려지지 않는다 (부드러운 스크롤이 끝날 때까지 기다린다)
    const form = page.locator("form").filter({ has: page.getByLabel("질문") });
    await expect
      .poll(async () => {
        const [markBox, formBox] = [await mark.boundingBox(), await form.boundingBox()];
        return formBox!.y - (markBox!.y + markBox!.height);
      })
      .toBeGreaterThanOrEqual(0);
    // 어두운 화면용 표시 색 (globals.css 의 --highlight)
    await expect(mark).toHaveCSS("background-color", "rgb(74, 63, 20)");
    // 가로 스크롤이 생기지 않는다
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
