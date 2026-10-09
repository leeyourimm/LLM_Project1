"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useApp } from "@/components/providers";
import { ErrorBox, Loading } from "@/components/ui";
import { post } from "@/lib/api";
import { hideTokenFromAddressBar, linkToken } from "@/lib/token";

export function VerifyEmail() {
  const { auth, refreshAuth } = useApp();
  const params = useSearchParams();
  const [token] = useState(() => linkToken(params.get("token")));
  const [state, setState] = useState<"working" | "done" | "error">(token ? "working" : "error");
  const [error, setError] = useState<unknown>(null);
  // 개발 모드(StrictMode)에서 effect 가 두 번 돌아도 한 번만 보낸다 (토큰은 한 번만 쓸 수 있음)
  const sent = useRef(false);

  useEffect(() => {
    hideTokenFromAddressBar("/verify-email");
    if (!token || sent.current) return;
    sent.current = true;
    post("/api/auth/verify-email", { token })
      .then(async () => {
        setState("done");
        await refreshAuth();
      })
      .catch((err) => {
        setError(err);
        setState("error");
      });
  }, [token, refreshAuth]);

  const next = auth?.user ? (
    <Link className="link" href="/account">
      계정 화면으로
    </Link>
  ) : (
    <Link className="link" href="/login">
      로그인하기
    </Link>
  );

  return (
    <section className="card mx-auto max-w-sm space-y-3" aria-live="polite">
      <h1 className="text-xl font-bold">이메일 인증</h1>
      {state === "working" ? <Loading label="확인하는 중…" /> : null}
      {state === "done" ? <p className="text-sm text-good">이메일을 인증했습니다. 이제 이메일 알림을 켤 수 있습니다.</p> : null}
      {state === "error" ? (
        error ? (
          <ErrorBox error={error} />
        ) : (
          <p className="text-sm">링크가 올바르지 않습니다. 계정 화면에서 인증 메일을 다시 받아 주세요.</p>
        )
      ) : null}
      {state !== "working" ? <p className="text-sm">{next}</p> : null}
    </section>
  );
}
