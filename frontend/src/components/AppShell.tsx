"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useApp } from "./providers";
import { Loading } from "./ui";

const NAV = [
  { href: "/", label: "질문하기" },
  { href: "/company", label: "회사" },
  { href: "/compare", label: "비교" },
  { href: "/feed", label: "공시 피드" },
  { href: "/watchlist", label: "관심 종목" },
  { href: "/diff", label: "변경점" },
];
// 로그인 없이 열 수 있는 화면
const PUBLIC = ["/login", "/signup", "/terms", "/privacy", "/forgot-password", "/reset-password", "/verify-email"];

function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => {
    try {
      setTheme(localStorage.getItem("theme"));
    } catch {
      setTheme(null);
    }
  }, []);
  const next = theme === "dark" ? "light" : theme === "light" ? null : "dark";
  const label = theme === "dark" ? "어두운 화면" : theme === "light" ? "밝은 화면" : "시스템 설정";
  return (
    <button
      type="button"
      className="btn px-2 py-1 text-xs"
      title="화면 밝기 바꾸기"
      onClick={() => {
        setTheme(next);
        const root = document.documentElement;
        if (next) root.dataset.theme = next;
        else delete root.dataset.theme;
        try {
          if (next) localStorage.setItem("theme", next);
          else localStorage.removeItem("theme");
        } catch {
          // 저장소를 못 써도 이번 화면에는 적용된다
        }
      }}
    >
      {label}
    </button>
  );
}

// 가입 이메일 인증 전(EMAIL_VERIFICATION_REQUIRED=true)일 때 모든 화면 위에 보이는 안내
function VerifyBanner() {
  return (
    <div role="status" className="border-b border-warning/40 bg-warning-soft">
      <p className="mx-auto max-w-6xl px-4 py-2 text-sm">
        가입한 이메일을 아직 인증하지 않았습니다. 메일함의 인증 링크를 열어야 이메일 알림을 켤 수 있습니다.{" "}
        <Link href="/account" className="link">
          인증 메일 다시 받기
        </Link>
      </p>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { auth, logout } = useApp();
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC.some((p) => pathname.startsWith(p));
  const needLogin = auth?.auth_required && !auth.user && !isPublic;

  useEffect(() => {
    if (needLogin) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [needLogin, pathname, router]);

  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <Link href="/" className="mr-auto font-bold sm:mr-0">
            DART 공시 분석
          </Link>
          {!isPublic && !needLogin ? (
            <nav aria-label="주요 화면" className="order-last -mx-1 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto sm:flex-1">
              {NAV.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active(n.href) ? "page" : undefined}
                  className={`shrink-0 rounded-lg px-3 py-1.5 text-sm ${
                    active(n.href) ? "bg-accent-soft font-semibold text-accent" : "text-muted hover:text-ink"
                  }`}
                >
                  {n.label}
                </Link>
              ))}
            </nav>
          ) : (
            <div className="hidden flex-1 sm:block" />
          )}
          <div className="flex items-center gap-2 text-sm">
            <ThemeToggle />
            {auth?.user ? (
              <>
                <Link href="/account" className="text-muted hover:text-ink">
                  {auth.user.email}
                </Link>
                <button type="button" className="btn px-2 py-1 text-xs" onClick={() => logout()}>
                  로그아웃
                </button>
              </>
            ) : null}
          </div>
        </div>
      </header>
      {auth?.user && auth.email_verification_required && !auth.user.email_verified && !isPublic ? <VerifyBanner /> : null}
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        {auth === null || needLogin ? <Loading /> : children}
      </main>
      <footer className="border-t border-line px-4 py-6 text-center text-xs text-muted">
        공시 정보 요약이며 투자 권유가 아닙니다. 데이터 출처: 금융감독원 OpenDART ·{" "}
        <Link href="/terms" className="link">
          이용약관
        </Link>{" "}
        ·{" "}
        <Link href="/privacy" className="link">
          개인정보 처리방침
        </Link>
      </footer>
    </div>
  );
}
