"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { signupHref, timeLeft } from "@/lib/guest";
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

// 주요 화면 메뉴. 휴대폰처럼 좁은 화면에서는 한 줄로 두고 옆으로 밀어서 보는데, 넘친 쪽 끝을 흐리게 하고
// ‹ › 표시를 붙여 더 있다는 것을 보여 주고, 지금 화면의 메뉴는 가운데로 끌어온다. 다 보이는 넓은 화면은 그대로 둔다
function MainNav({ pathname }: { pathname: string }) {
  const ref = useRef<HTMLElement>(null);
  const [edge, setEdge] = useState({ left: false, right: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const left = el.scrollLeft > 1;
      const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
      setEdge((e) => (e.left === left && e.right === right ? e : { left, right }));
    };
    const center = () => {
      const current = el.querySelector<HTMLElement>('[aria-current="page"]');
      if (current) {
        const box = el.getBoundingClientRect();
        const item = current.getBoundingClientRect();
        el.scrollLeft += item.left - box.left - (box.width - item.width) / 2;
      }
      update();
    };
    center();
    el.addEventListener("scroll", update, { passive: true });
    // 화면 폭이 바뀌거나, 계정 단추나 글꼴이 늦게 와서 메뉴 폭이 바뀌면 다시 맞춘다
    const ro = new ResizeObserver(center);
    ro.observe(el);
    for (const item of el.children) ro.observe(item);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [pathname]);

  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  const fade =
    edge.left || edge.right
      ? `linear-gradient(to right, ${edge.left ? "transparent, #000 2rem" : "#000"}, ${edge.right ? "#000 calc(100% - 2rem), transparent" : "#000"})`
      : undefined;

  const cue = "pointer-events-none absolute inset-y-0 flex items-center text-lg leading-none text-muted";

  return (
    <div className="relative order-last -mx-1 w-full min-w-0 sm:order-none sm:w-auto sm:flex-1">
      <nav
        ref={ref}
        aria-label="주요 화면"
        className="flex gap-1 overflow-x-auto"
        style={fade ? { maskImage: fade, WebkitMaskImage: fade } : undefined}
      >
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
      {edge.left ? (
        <span aria-hidden className={`${cue} left-0`}>
          ‹
        </span>
      ) : null}
      {edge.right ? (
        <span aria-hidden className={`${cue} right-0`}>
          ›
        </span>
      ) : null}
    </div>
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

// 체험 계정으로 둘러보는 동안 모든 화면 위에 보이는 안내
function GuestBanner({ expiresAt, canSignup, pathname }: { expiresAt?: string; canSignup: boolean; pathname: string }) {
  return (
    <div role="status" aria-label="체험 계정 안내" className="border-b border-accent/30 bg-accent-soft">
      <p className="mx-auto max-w-6xl px-4 py-2 text-sm">
        가입 없이 체험하는 중입니다. 기록은 {timeLeft(expiresAt)} 뒤 지워집니다.{" "}
        {canSignup ? (
          <Link href={signupHref(pathname)} className="link font-semibold">
            가입하고 이어서 쓰기
          </Link>
        ) : null}
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

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <Link href="/" className="mr-auto font-bold sm:mr-0">
            DART 공시 분석
          </Link>
          {!isPublic && !needLogin ? (
            <MainNav pathname={pathname} />
          ) : (
            <div className="hidden flex-1 sm:block" />
          )}
          <div className="flex items-center gap-2 text-sm">
            <ThemeToggle />
            {auth?.user?.guest ? (
              <>
                <Link href="/account" className="text-muted hover:text-ink">
                  체험 계정
                </Link>
                <button
                  type="button"
                  className="btn px-2 py-1 text-xs"
                  title="체험 기록을 지우고 나갑니다"
                  onClick={() => logout()}
                >
                  체험 끝내기
                </button>
              </>
            ) : auth?.user ? (
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
      {auth?.user?.guest && !pathname.startsWith("/signup") ? (
        <GuestBanner expiresAt={auth.user.guest_expires_at} canSignup={auth.allow_signup} pathname={pathname} />
      ) : null}
      {auth?.user && !auth.user.guest && auth.email_verification_required && !auth.user.email_verified && !isPublic ? <VerifyBanner /> : null}
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
