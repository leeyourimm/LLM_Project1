"use client";

import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/components/providers";
import { ErrorBox, PageTitle } from "@/components/ui";
import { api, del, download, post } from "@/lib/api";
import type { TwoFactorStatus } from "@/lib/types";
import { TwoFactorCard } from "./TwoFactorCard";

export default function AccountPage() {
  const { auth } = useApp();
  const [twoFactor, setTwoFactor] = useState<TwoFactorStatus | null>(null);
  const signedIn = Boolean(auth?.user);
  const loadTwoFactor = useCallback(() => {
    api<TwoFactorStatus>("/api/auth/2fa")
      .then(setTwoFactor)
      .catch(() => setTwoFactor(null));
  }, []);
  useEffect(() => {
    if (signedIn) loadTwoFactor();
  }, [signedIn, loadTwoFactor]);

  if (!auth?.user) return <p className="card text-sm">로그인 없이 쓰는 설정에서는 계정 화면이 없습니다.</p>;
  return (
    <div className="max-w-lg space-y-4">
      <PageTitle title="계정" sub={auth.user.email} />
      <EmailCard />
      <PasswordForm />
      <TwoFactorCard status={twoFactor} reload={loadTwoFactor} />
      <ExportCard />
      <DeleteAccount needsCode={Boolean(twoFactor?.enabled)} />
    </div>
  );
}

function EmailCard() {
  const { auth, refreshAuth } = useApp();
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const verified = Boolean(auth?.user?.email_verified);

  return (
    <section className="card space-y-3" aria-labelledby="email-title">
      <h2 id="email-title" className="font-semibold">
        이메일 인증
      </h2>
      {verified ? (
        <p className="text-sm text-good">인증된 이메일입니다. 처음 보는 기기에서 로그인하면 이 주소로 알려 드립니다.</p>
      ) : auth?.email_enabled ? (
        <>
          <p className="text-sm">
            아직 인증하지 않았습니다.{" "}
            {auth.email_verification_required ? "인증해야 이메일 알림을 켤 수 있습니다." : "인증하면 새 기기 로그인 알림을 받을 수 있습니다."}
          </p>
          {error ? <ErrorBox error={error} /> : null}
          {note ? (
            <p role="status" className="text-sm">
              {note}
            </p>
          ) : null}
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              setNote(null);
              try {
                const r = await post<{ sent_to?: string; already_verified?: boolean }>("/api/auth/verify-email/resend");
                if (r.already_verified) await refreshAuth();
                else setNote(`${r.sent_to}로 인증 메일을 보냈습니다. 24시간 안에 메일의 링크를 열어 주세요.`);
              } catch (err) {
                setError(err);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "보내는 중…" : "인증 메일 다시 받기"}
          </button>
        </>
      ) : (
        <p className="text-sm text-muted">이 서버에는 메일 발송이 설정되지 않아 이메일 인증을 쓰지 않습니다.</p>
      )}
    </section>
  );
}

function PasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);

  return (
    <form
      className="card space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        setDone(false);
        try {
          await post("/api/auth/password", { current, new: next });
          setDone(true);
          setCurrent("");
          setNext("");
        } catch (err) {
          setError(err);
        }
      }}
    >
      <h2 className="font-semibold">비밀번호 바꾸기</h2>
      <div>
        <label htmlFor="cur" className="label">
          지금 비밀번호
        </label>
        <input id="cur" className="input" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
      </div>
      <div>
        <label htmlFor="new" className="label">
          새 비밀번호 (10자 이상)
        </label>
        <input id="new" className="input" type="password" autoComplete="new-password" required minLength={10} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} />
      </div>
      {error ? <ErrorBox error={error} /> : null}
      {done ? <p role="status" className="text-sm text-good">바꿨습니다. 다른 기기에서는 다시 로그인해야 합니다.</p> : null}
      <button className="btn btn-primary" type="submit">
        바꾸기
      </button>
    </form>
  );
}

function ExportCard() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  return (
    <section className="card space-y-3" aria-labelledby="export-title">
      <h2 id="export-title" className="font-semibold">
        내 데이터
      </h2>
      <p className="text-sm text-muted">
        계정 정보, 관심 종목, 알림 설정(웹 푸시를 받는 브라우저 포함), 2단계 인증 사용 여부, 질문·답변 기록과 평가를 JSON 파일 하나로
        내려받습니다. 비밀번호, 2단계 인증 키, 복구 코드는 들어 있지 않습니다.
      </p>
      {error ? <ErrorBox error={error} /> : null}
      <button
        type="button"
        className="btn"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await download("/api/account/export", "dartrag-export.json");
          } catch (err) {
            setError(err);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "준비하는 중…" : "내 데이터 내려받기"}
      </button>
    </section>
  );
}

function DeleteAccount({ needsCode }: { needsCode: boolean }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  return (
    <form
      className="card space-y-3 border-critical/40"
      aria-labelledby="delete-title"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await del("/api/account", needsCode ? { password, code } : { password });
          // 화면 상태를 새로 읽으면 로그인 확인이 먼저 돌아 /login?next=/account 로 가므로,
          // 페이지를 새로 열어 깨끗한 로그인 화면으로 보낸다
          window.location.replace("/login");
        } catch (err) {
          setError(err);
          setBusy(false);
        }
      }}
    >
      <h2 id="delete-title" className="font-semibold text-critical">
        계정 삭제
      </h2>
      <div role="note" className="rounded-lg border border-critical/30 bg-critical-soft px-4 py-3 text-sm">
        <p className="font-semibold">삭제하면 되돌릴 수 없습니다.</p>
        <p className="mt-1">
          계정, 관심 종목, 이메일·텔레그램·웹 푸시 알림 설정, 2단계 인증, 질문·답변 기록과 평가가 바로 모두 지워지고 모든 기기에서
          로그아웃됩니다. 필요하면 먼저 내 데이터를 내려받으세요.
        </p>
      </div>
      <div>
        <label htmlFor="delete-password" className="label">
          지금 비밀번호
        </label>
        <input
          id="delete-password"
          className="input"
          type="password"
          autoComplete="current-password"
          required
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {needsCode ? (
        <div>
          <label htmlFor="delete-code" className="label">
            인증 코드 또는 복구 코드
          </label>
          <input
            id="delete-code"
            className="input font-mono"
            autoComplete="one-time-code"
            autoCapitalize="off"
            spellCheck={false}
            required
            maxLength={20}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
      ) : null}
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" required checked={confirm} onChange={(e) => setConfirm(e.target.checked)} className="mt-1" />
        <span>모든 기록이 지워지고 되돌릴 수 없다는 것을 이해했습니다.</span>
      </label>
      {error ? <ErrorBox error={error} /> : null}
      <button className="btn border-critical text-critical" type="submit" disabled={busy || !confirm || !password || (needsCode && !code)}>
        {busy ? "삭제하는 중…" : "계정 삭제"}
      </button>
    </form>
  );
}
