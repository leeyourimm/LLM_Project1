"use client";

import { useEffect, useRef, useState } from "react";
import { ErrorBox } from "@/components/ui";
import { ApiError, post } from "@/lib/api";
import type { LoginResult } from "@/lib/types";

// 로그인 두 번째 단계: 비밀번호(또는 재설정 링크)를 맞힌 뒤 인증 앱 코드나 복구 코드를 받는다.
// 이 단계는 서버의 HttpOnly 쿠키(5분, 5번까지)로 이어진다.
export function TwoFactorStep({
  onDone,
  onRestart,
  intro,
}: {
  onDone: (result: LoginResult) => void | Promise<void>;
  onRestart: () => void;
  intro?: React.ReactNode;
}) {
  const [recovery, setRecovery] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [expired, setExpired] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, [recovery]);

  return (
    <form
      className="card mx-auto max-w-sm space-y-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const r = await post<LoginResult>("/api/auth/login/2fa", { code });
          await onDone(r);
        } catch (err) {
          setError(err);
          setCode("");
          if (err instanceof ApiError && (err.body as { restart?: boolean } | null)?.restart) setExpired(true);
          setBusy(false);
        }
      }}
    >
      <h1 className="text-xl font-bold">2단계 인증</h1>
      {intro}
      <p className="text-sm text-muted">
        {recovery
          ? "인증 앱을 쓸 수 없으면 2단계 인증을 켤 때 저장한 복구 코드 하나를 넣으세요. 복구 코드는 한 번만 쓸 수 있습니다."
          : "인증 앱(Google Authenticator 등)에 보이는 6자리 코드를 넣으세요."}
      </p>
      {expired ? null : (
        <div>
          <label htmlFor="two-factor-code" className="label">
            {recovery ? "복구 코드" : "인증 코드"}
          </label>
          <input
            ref={input}
            id="two-factor-code"
            className="input font-mono tracking-widest"
            inputMode={recovery ? "text" : "numeric"}
            autoComplete="one-time-code"
            autoCapitalize="off"
            spellCheck={false}
            required
            maxLength={recovery ? 20 : 8}
            pattern={recovery ? undefined : "[0-9 ]{6,8}"}
            placeholder={recovery ? "xxxx-xxxx-xxxx" : "123456"}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
      )}
      {error ? <ErrorBox error={error} /> : null}
      {expired ? (
        <button type="button" className="btn btn-primary w-full" onClick={onRestart}>
          처음부터 다시 로그인
        </button>
      ) : (
        <>
          <button className="btn btn-primary w-full" type="submit" disabled={busy}>
            {busy ? "확인 중…" : "확인"}
          </button>
          <div className="flex flex-wrap justify-between gap-2 text-sm">
            <button
              type="button"
              className="link"
              onClick={() => {
                setRecovery(!recovery);
                setCode("");
                setError(null);
              }}
            >
              {recovery ? "인증 앱 코드 넣기" : "복구 코드 쓰기"}
            </button>
            <button type="button" className="link" onClick={onRestart}>
              처음으로
            </button>
          </div>
        </>
      )}
    </form>
  );
}

/** 복구 코드로 로그인했을 때 남은 개수를 알린다. 없으면 null. */
export function recoveryNotice(r: LoginResult): string | null {
  if (r.method !== "recovery") return null;
  const left = r.recovery_codes_left ?? 0;
  return left > 3
    ? `복구 코드로 로그인했습니다. 남은 복구 코드는 ${left}개입니다.`
    : `복구 코드로 로그인했습니다. 남은 복구 코드가 ${left}개뿐입니다. 계정 화면에서 새로 만들어 두세요.`;
}
