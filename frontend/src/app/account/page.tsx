"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/providers";
import { ErrorBox, PageTitle } from "@/components/ui";
import { del, download, post } from "@/lib/api";

export default function AccountPage() {
  const { auth } = useApp();
  if (!auth?.user) return <p className="card text-sm">로그인 없이 쓰는 설정에서는 계정 화면이 없습니다.</p>;
  return (
    <div className="max-w-lg space-y-4">
      <PageTitle title="계정" sub={auth.user.email} />
      <PasswordForm />
      <ExportCard />
      <DeleteAccount />
    </div>
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
      <p className="text-sm text-muted">계정 정보, 관심 종목, 알림 설정, 질문·답변 기록과 평가를 JSON 파일 하나로 내려받습니다. 비밀번호는 들어 있지 않습니다.</p>
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

function DeleteAccount() {
  const { refreshAuth } = useApp();
  const router = useRouter();
  const [password, setPassword] = useState("");
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
          await del("/api/account", { password });
          await refreshAuth();
          router.replace("/login");
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
        <p className="mt-1">계정, 관심 종목, 이메일·텔레그램 알림 설정, 질문·답변 기록과 평가가 바로 모두 지워지고 모든 기기에서 로그아웃됩니다. 필요하면 먼저 내 데이터를 내려받으세요.</p>
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
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" required checked={confirm} onChange={(e) => setConfirm(e.target.checked)} className="mt-1" />
        <span>모든 기록이 지워지고 되돌릴 수 없다는 것을 이해했습니다.</span>
      </label>
      {error ? <ErrorBox error={error} /> : null}
      <button className="btn border-critical text-critical" type="submit" disabled={busy || !confirm || !password}>
        {busy ? "삭제하는 중…" : "계정 삭제"}
      </button>
    </form>
  );
}
