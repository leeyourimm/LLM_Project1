"use client";

import { useState } from "react";
import { useApp } from "@/components/providers";
import { ErrorBox, PageTitle } from "@/components/ui";
import { post } from "@/lib/api";

export default function AccountPage() {
  const { auth } = useApp();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (!auth?.user) return <p className="card text-sm">로그인 없이 쓰는 설정에서는 계정 화면이 없습니다.</p>;
  return (
    <div className="max-w-lg space-y-4">
      <PageTitle title="계정" sub={auth.user.email} />
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
    </div>
  );
}
