"use client";

import Link from "next/link";
import { useState } from "react";
import { useApp } from "@/components/providers";
import { ErrorBox } from "@/components/ui";
import { post } from "@/lib/api";

export function ForgotForm() {
  const { auth } = useApp();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  if (auth && !auth.auth_required) {
    return (
      <p className="card text-sm">
        로그인 없이 쓰는 설정입니다. <Link className="link" href="/">처음 화면으로</Link>
      </p>
    );
  }

  if (auth && !auth.email_enabled) {
    return (
      <section className="card mx-auto max-w-sm space-y-3" aria-labelledby="forgot-title">
        <h1 id="forgot-title" className="text-xl font-bold">
          비밀번호 재설정
        </h1>
        <p className="text-sm">
          이 서버에는 메일 발송이 설정되지 않아 메일로 비밀번호를 재설정할 수 없습니다. 서비스 운영자에게 비밀번호 변경을 요청해 주세요.
        </p>
        <p className="text-xs text-muted">
          운영자는 서버에서 <code>dartrag user password 이메일</code>로 바꿀 수 있습니다.
        </p>
        <p className="text-sm">
          <Link className="link" href="/login">
            로그인으로 돌아가기
          </Link>
        </p>
      </section>
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
          const r = await post<{ message: string }>("/api/auth/password/forgot", { email });
          setSent(r.message);
        } catch (err) {
          setError(err);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h1 className="text-xl font-bold">비밀번호 재설정</h1>
      {sent ? (
        <p role="status" className="text-sm">
          {sent}
        </p>
      ) : (
        <>
          <p className="text-sm text-muted">가입한 이메일을 넣으면 새 비밀번호를 정하는 링크를 보내 드립니다.</p>
          <div>
            <label htmlFor="email" className="label">
              이메일
            </label>
            <input id="email" className="input" type="email" autoComplete="username" required maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          {error ? <ErrorBox error={error} /> : null}
          <button className="btn btn-primary w-full" type="submit" disabled={busy}>
            {busy ? "보내는 중…" : "재설정 링크 받기"}
          </button>
        </>
      )}
      <p className="text-sm text-muted">
        <Link className="link" href="/login">
          로그인으로 돌아가기
        </Link>
      </p>
    </form>
  );
}
