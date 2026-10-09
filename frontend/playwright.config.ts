import { defineConfig, devices } from "@playwright/test";

// 브라우저 종단 테스트. 실제 FastAPI 앱(가짜 검색·언어 모델, 실제 Postgres)과 `next start` 를 띄워
// 가입부터 탈퇴까지 화면으로 확인한다.
//
//   API_ORIGIN=http://127.0.0.1:8765 npm run build
//   E2E_DATABASE_URL=postgresql://dartrag:dartrag@127.0.0.1:5432/dartrag_e2e npm run e2e
//
// /api 를 백엔드로 넘기는 주소(API_ORIGIN)는 빌드할 때 정해지므로 위처럼 테스트용 백엔드 주소로 빌드한다.
// 백엔드는 저장소 루트에서 `python -m tests.e2e_server` 로 뜨므로 `pip install -e ".[dev]"` 가 되어 있어야 한다.
const API_PORT = Number(process.env.E2E_API_PORT ?? 8765);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const WEB = `http://127.0.0.1:${WEB_PORT}`;
const API = `http://127.0.0.1:${API_PORT}`;

if (!process.env.E2E_DATABASE_URL) {
  throw new Error("E2E_DATABASE_URL 이 필요합니다 (테스트용 Postgres 주소)");
}

export default defineConfig({
  testDir: "./e2e",
  // 탈퇴 때 추적 삭제 요청 횟수를 세는 검사가 있어 하나씩 돈다 (전체 몇 초)
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: WEB,
    trace: "retain-on-failure",
    locale: "ko-KR",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `${process.env.E2E_PYTHON ?? "python"} -m tests.e2e_server`,
      cwd: "..",
      url: `${API}/api/health`,
      env: {
        // 설치된 패키지 대신 이 저장소의 src 를 쓴다 (pytest 의 pythonpath 와 같음)
        PYTHONPATH: "src",
        E2E_DATABASE_URL: process.env.E2E_DATABASE_URL,
        E2E_API_PORT: String(API_PORT),
        E2E_WEB_ORIGIN: WEB,
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx next start -H 127.0.0.1 -p ${WEB_PORT}`,
      url: `${WEB}/login`,
      env: { NEXT_TELEMETRY_DISABLED: "1" },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
