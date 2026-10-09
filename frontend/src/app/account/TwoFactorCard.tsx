"use client";

import { useState } from "react";
import { ErrorBox } from "@/components/ui";
import { del, post } from "@/lib/api";
import type { TwoFactorSetup, TwoFactorStatus } from "@/lib/types";

// 계정 화면의 2단계 인증 카드: 켜기(QR·직접 입력 키 → 첫 코드 확인 → 복구 코드), 복구 코드 새로 받기, 끄기
export function TwoFactorCard({ status, reload }: { status: TwoFactorStatus | null; reload: () => void }) {
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);

  if (!status) return null;
  return (
    <section className="card space-y-3" aria-labelledby="two-factor-title">
      <h2 id="two-factor-title" className="font-semibold">
        2단계 인증
      </h2>
      {codes ? (
        <RecoveryCodes
          codes={codes}
          fresh={!status.enabled || Boolean(setup)}
          onDone={() => {
            setCodes(null);
            setSetup(null);
            reload();
          }}
        />
      ) : status.enabled ? (
        <Enabled status={status} reload={reload} onCodes={setCodes} />
      ) : setup ? (
        <Enroll
          setup={setup}
          onCancel={() => setSetup(null)}
          onEnabled={(c) => {
            setCodes(c);
            reload();
          }}
        />
      ) : (
        <Start onSetup={setSetup} />
      )}
    </section>
  );
}

function Start({ onSetup }: { onSetup: (s: TwoFactorSetup) => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          onSetup(await post<TwoFactorSetup>("/api/auth/2fa/setup", { password }));
        } catch (err) {
          setError(err);
          setBusy(false);
        }
      }}
    >
      <p className="text-sm">
        켜면 로그인할 때 비밀번호에 더해 휴대폰 인증 앱(Google Authenticator, Microsoft Authenticator, 1Password 등)의 6자리 코드를
        넣습니다. 비밀번호가 새어도 휴대폰 없이는 로그인할 수 없습니다.
      </p>
      <div>
        <label htmlFor="tf-setup-password" className="label">
          지금 비밀번호
        </label>
        <input
          id="tf-setup-password"
          className="input"
          type="password"
          autoComplete="current-password"
          required
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {error ? <ErrorBox error={error} /> : null}
      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? "준비하는 중…" : "2단계 인증 켜기"}
      </button>
    </form>
  );
}

function Enroll({ setup, onCancel, onEnabled }: { setup: TwoFactorSetup; onCancel: () => void; onEnabled: (codes: string[]) => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const r = await post<{ recovery_codes: string[] }>("/api/auth/2fa/enable", { code });
          onEnabled(r.recovery_codes);
        } catch (err) {
          setError(err);
          setCode("");
          setBusy(false);
        }
      }}
    >
      <ol className="list-decimal space-y-3 pl-5 text-sm">
        <li>
          <p>인증 앱에서 QR 코드를 찍으세요.</p>
          <img src={setup.qr} alt="인증 앱 등록용 QR 코드" width={200} height={200} className="mt-2 rounded-lg border border-line bg-white" />
        </li>
        <li>
          <p>찍을 수 없으면 앱에서 &ldquo;키 직접 입력&rdquo;을 고르고 아래 키를 넣으세요 (시간 기준, 6자리).</p>
          <p className="mt-1 text-xs text-muted">
            계정 이름: {setup.issuer} ({setup.account})
          </p>
          <p id="tf-secret" className="mt-1 select-all break-all rounded-lg bg-surface-2 px-3 py-2 font-mono text-sm" aria-label="직접 입력 키">
            {setup.secret}
          </p>
          <p className="mt-1 text-xs">
            이 휴대폰에서 보고 있다면{" "}
            <a className="link" href={setup.otpauth_uri}>
              인증 앱에서 바로 열기
            </a>
          </p>
        </li>
        <li>
          <label htmlFor="tf-enable-code" className="label">
            인증 앱에 보이는 6자리 코드
          </label>
          <input
            id="tf-enable-code"
            className="input font-mono tracking-widest"
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            maxLength={8}
            pattern="[0-9 ]{6,8}"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </li>
      </ol>
      <p className="text-xs text-muted">이 키는 비밀번호처럼 다루세요. 화면을 닫으면 다시 보여 주지 않으니 처음부터 다시 켜야 합니다.</p>
      {error ? <ErrorBox error={error} /> : null}
      <div className="flex gap-2">
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "확인 중…" : "확인하고 켜기"}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          취소
        </button>
      </div>
    </form>
  );
}

function RecoveryCodes({ codes, fresh, onDone }: { codes: string[]; fresh: boolean; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const text = `DART 공시 분석 2단계 인증 복구 코드 (한 번씩만 쓸 수 있음)\n\n${codes.join("\n")}\n`;

  return (
    <div className="space-y-3">
      <p role="status" className="text-sm text-good">
        {fresh ? "2단계 인증을 켰습니다. 다른 기기에서는 다시 로그인해야 합니다." : "복구 코드를 새로 만들었습니다. 예전 복구 코드는 이제 쓸 수 없습니다."}
      </p>
      <div role="note" className="rounded-lg border border-warning/40 bg-warning-soft px-4 py-3 text-sm">
        휴대폰을 잃어버리면 아래 복구 코드로만 로그인할 수 있습니다. 지금 저장하세요. 이 화면을 닫으면 다시 볼 수 없습니다.
      </div>
      <ol aria-label="복구 코드" className="grid grid-cols-2 gap-2 font-mono text-sm">
        {codes.map((c) => (
          <li key={c} className="rounded bg-surface-2 px-2 py-1 text-center">
            {c}
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setNote("복사했습니다.");
            } catch {
              setNote("복사할 수 없습니다. 파일로 받거나 직접 적어 두세요.");
            }
          }}
        >
          복사
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
            const a = document.createElement("a");
            a.href = url;
            a.download = "dartrag-recovery-codes.txt";
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}
        >
          파일로 받기
        </button>
      </div>
      {note ? <p className="text-xs text-muted">{note}</p> : null}
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} className="mt-1" />
        <span>복구 코드를 안전한 곳에 저장했습니다.</span>
      </label>
      <button type="button" className="btn btn-primary" disabled={!saved} onClick={onDone}>
        완료
      </button>
    </div>
  );
}

function Enabled({ status, reload, onCodes }: { status: TwoFactorStatus; reload: () => void; onCodes: (codes: string[]) => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const left = status.recovery_codes_left;

  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        const action = ((e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null)?.value;
        setBusy(true);
        setError(null);
        try {
          if (action === "off") {
            await del("/api/auth/2fa", { password, code });
            reload();
          } else {
            const r = await post<{ recovery_codes: string[] }>("/api/auth/2fa/recovery-codes", { password, code });
            onCodes(r.recovery_codes);
          }
        } catch (err) {
          setError(err);
          setCode("");
          setBusy(false);
        }
      }}
    >
      <p className="text-sm text-good">켜져 있습니다. 로그인할 때 인증 앱 코드를 함께 넣습니다.</p>
      <p className={`text-sm ${left <= 3 ? "text-critical" : "text-muted"}`}>
        남은 복구 코드 {left}개{left <= 3 ? " — 다 쓰기 전에 새로 받아 두세요." : ""}
      </p>
      <p className="text-xs text-muted">복구 코드를 새로 받거나 끄려면 비밀번호와 인증 앱 코드(또는 복구 코드)를 넣으세요.</p>
      <div>
        <label htmlFor="tf-password" className="label">
          지금 비밀번호
        </label>
        <input
          id="tf-password"
          className="input"
          type="password"
          autoComplete="current-password"
          required
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="tf-code" className="label">
          인증 코드 또는 복구 코드
        </label>
        <input
          id="tf-code"
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
      {error ? <ErrorBox error={error} /> : null}
      <div className="flex flex-wrap gap-2">
        <button className="btn" type="submit" value="codes" disabled={busy}>
          복구 코드 새로 받기
        </button>
        <button className="btn border-critical text-critical" type="submit" value="off" disabled={busy}>
          2단계 인증 끄기
        </button>
      </div>
    </form>
  );
}
