"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useApp } from "@/components/providers";
import { TwoFactorStep, recoveryNotice } from "@/components/TwoFactorStep";
import { ErrorBox } from "@/components/ui";
import { post } from "@/lib/api";
import { hideTokenFromAddressBar, linkToken } from "@/lib/token";
import type { LoginResult } from "@/lib/types";

export function ResetForm() {
  const { refreshAuth } = useApp();
  const router = useRouter();
  const params = useSearchParams();
  // 처음 열 때 한 번만 읽고 주소창에서는 지운다
  const [token] = useState(() => linkToken(params.get("token")));
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // 2단계 인증을 켠 계정은 비밀번호를 바꾼 뒤에도 코드를 넣어야 로그인된다
  const [codeStep, setCodeStep] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    hideTokenFromAddressBar("/reset-password");
  }, []);

  if (notice) {
    return (
      <section className="card mx-auto max-w-sm space-y-4">
        <h1 className="text-xl font-bold">비밀번호를 바꿨습니다</h1>
        <p role="status" className="text-sm">
          {notice}
        </p>
        <button type="button" className="btn btn-primary w-full" onClick={() => router.replace("/")}>
          계속
        </button>
      </section>
    );
  }

  if (codeStep) {
    return (
      <TwoFactorStep
        intro={<p className="text-sm">새 비밀번호로 바꿨습니다. 2단계 인증을 켠 계정이라 코드를 넣어야 로그인됩니다.</p>}
        onDone={async (r) => {
          await refreshAuth();
          const n = recoveryNotice(r);
          if (n) setNotice(n);
          else router.replace("/");
        }}
        onRestart={() => router.replace("/login")}
      />
    );
  }

  if (!token) {
    return (
      <section className="card mx-auto max-w-sm space-y-3">
        <h1 className="text-xl font-bold">링크가 올바르지 않습니다</h1>
        <p className="text-sm">메일의 링크를 다시 열거나, 재설정 링크를 새로 받아 주세요.</p>
        <Link className="link text-sm" href="/forgot-password">
          재설정 링크 다시 받기
        </Link>
      </section>
    );
  }

  const mismatch = confirm.length > 0 && confirm !== password;

  return (
    <form
      className="card mx-auto max-w-sm space-y-4"
      onSubmit={async (e) => {
        e.preventDefault();
        if (mismatch) return;
        setBusy(true);
        setError(null);
        try {
          const r = await post<LoginResult>("/api/auth/password/reset", { token, password });
          if (r?.two_factor) {
            setCodeStep(true);
            return;
          }
          await refreshAuth();
          router.replace("/");
        } catch (err) {
          setError(err);
          setBusy(false);
        }
      }}
    >
      <h1 className="text-xl font-bold">새 비밀번호 정하기</h1>
      <p className="text-sm text-muted">
        바꾸면 다른 모든 기기에서 로그아웃되고, 이 기기에서는 바로 로그인됩니다. 2단계 인증을 켰다면 인증 코드도 넣어야 합니다.
      </p>
      <div>
        <label htmlFor="new-password" className="label">
          새 비밀번호 (10자 이상)
        </label>
        <input
          id="new-password"
          className="input"
          type="password"
          autoComplete="new-password"
          required
          minLength={10}
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="confirm-password" className="label">
          새 비밀번호 확인
        </label>
        <input
          id="confirm-password"
          className="input"
          type="password"
          autoComplete="new-password"
          required
          maxLength={128}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          aria-invalid={mismatch}
          aria-describedby={mismatch ? "confirm-hint" : undefined}
        />
        {mismatch ? (
          <p id="confirm-hint" className="mt-1 text-xs text-critical">
            두 비밀번호가 다릅니다.
          </p>
        ) : null}
      </div>
      {error ? <ErrorBox error={error} /> : null}
      <button className="btn btn-primary w-full" type="submit" disabled={busy || mismatch}>
        {busy ? "바꾸는 중…" : "비밀번호 바꾸기"}
      </button>
      <p className="text-sm text-muted">
        링크가 만료됐다면{" "}
        <Link className="link" href="/forgot-password">
          다시 받기
        </Link>
      </p>
    </form>
  );
}
