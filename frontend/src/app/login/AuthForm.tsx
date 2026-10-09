"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/providers";
import { TwoFactorStep, recoveryNotice } from "@/components/TwoFactorStep";
import { ErrorBox } from "@/components/ui";
import { post } from "@/lib/api";
import { safeNext } from "@/lib/redirect";
import type { LoginResult } from "@/lib/types";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const { auth, refreshAuth } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next");
  const target = safeNext(next);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // 2단계 인증을 켠 계정: 비밀번호를 맞히면 코드 단계로 넘어간다
  const [codeStep, setCodeStep] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const signup = mode === "signup";

  if (auth && !auth.auth_required) {
    return <p className="card text-sm">로그인 없이 쓰는 설정입니다. <Link className="link" href="/">처음 화면으로</Link></p>;
  }

  if (notice) {
    return (
      <section className="card mx-auto max-w-sm space-y-4">
        <h1 className="text-xl font-bold">로그인했습니다</h1>
        <p role="status" className="text-sm">
          {notice}
        </p>
        <button type="button" className="btn btn-primary w-full" onClick={() => router.replace(target)}>
          계속
        </button>
      </section>
    );
  }

  if (codeStep) {
    return (
      <TwoFactorStep
        onDone={async (r) => {
          await refreshAuth();
          const n = recoveryNotice(r);
          if (n) setNotice(n);
          else router.replace(target);
        }}
        onRestart={() => {
          setCodeStep(false);
          setPassword("");
          setError(null);
        }}
      />
    );
  }

  return (
    <form
      className="card mx-auto max-w-sm space-y-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const r = await post<LoginResult>(signup ? "/api/auth/signup" : "/api/auth/login", { email, password });
          if (r?.two_factor) {
            setCodeStep(true);
            return;
          }
          await refreshAuth();
          router.replace(target);
        } catch (err) {
          setError(err);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h1 className="text-xl font-bold">{signup ? "가입하기" : "로그인"}</h1>
      <div>
        <label htmlFor="email" className="label">
          이메일
        </label>
        <input id="email" className="input" type="email" autoComplete="username" required maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div>
        <label htmlFor="password" className="label">
          비밀번호
        </label>
        <input
          id="password"
          className="input"
          type="password"
          autoComplete={signup ? "new-password" : "current-password"}
          required
          minLength={signup ? 10 : undefined}
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-describedby={signup ? "pw-hint" : undefined}
        />
        {signup ? (
          <p id="pw-hint" className="mt-1 text-xs text-muted">
            10자 이상으로 정해 주세요.
          </p>
        ) : (
          <p className="mt-1 text-right text-xs">
            <Link className="link" href="/forgot-password">
              비밀번호를 잊으셨나요?
            </Link>
          </p>
        )}
      </div>
      {signup ? (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" required checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1" />
          <span>
            <Link className="link" href="/terms" target="_blank">
              이용약관
            </Link>
            과{" "}
            <Link className="link" href="/privacy" target="_blank">
              개인정보 처리방침
            </Link>
            에 동의합니다.
          </span>
        </label>
      ) : null}
      {error ? <ErrorBox error={error} /> : null}
      <button className="btn btn-primary w-full" type="submit" disabled={busy}>
        {signup ? "가입하기" : "로그인"}
      </button>
      {signup ? (
        <p className="text-sm text-muted">
          이미 계정이 있으면{" "}
          <Link className="link" href={`/login${next ? `?next=${encodeURIComponent(next)}` : ""}`}>
            로그인
          </Link>
        </p>
      ) : auth?.allow_signup ? (
        <p className="text-sm text-muted">
          계정이 없으면{" "}
          <Link className="link" href={`/signup${next ? `?next=${encodeURIComponent(next)}` : ""}`}>
            가입하기
          </Link>
        </p>
      ) : null}
    </form>
  );
}
