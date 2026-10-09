import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { AppShell } from "@/components/AppShell";
import { Providers } from "@/components/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "DART 공시 분석", template: "%s · DART 공시 분석" },
  description: "DART 공시를 근거로 답하는 기업 분석 서비스",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#1a1f25" },
  ],
};

// 저장한 화면 밝기를 그리기 전에 적용해 깜빡임을 막는다
const THEME_SCRIPT = `try{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}catch(e){}`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // 배포 환경에서는 Caddy 가 요청마다 만든 nonce 를 CSP 헤더와 x-nonce 로 넘긴다.
  // Next 는 CSP 헤더의 nonce 를 자기 스크립트에 붙이고, 이 인라인 스크립트에는 직접 붙인다.
  // (헤더를 읽으므로 모든 화면이 요청마다 그려진다. 개발 서버처럼 값이 없으면 nonce 없이 둔다)
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
