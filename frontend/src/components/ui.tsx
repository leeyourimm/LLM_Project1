"use client";

import { ApiError } from "@/lib/api";
import { IMPORTANCE } from "@/lib/types";

export function ErrorBox({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError || error instanceof Error ? error.message : "알 수 없는 오류가 생겼습니다";
  return (
    <div role="alert" className="rounded-lg border border-critical/30 bg-critical-soft px-4 py-3 text-sm">
      {message}
    </div>
  );
}

export function Loading({ label = "불러오는 중…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted">
      <span className="size-3 animate-pulse rounded-full bg-accent" aria-hidden />
      {label}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="card text-sm text-muted">{children}</div>;
}

export function ImportanceBadge({ level }: { level: number }) {
  const imp = IMPORTANCE[level] ?? IMPORTANCE[1];
  const tone =
    imp.tone === "critical"
      ? "bg-critical-soft text-critical"
      : imp.tone === "warning"
        ? "bg-warning-soft text-warning"
        : "bg-surface-2 text-muted";
  const icon = imp.tone === "critical" ? "●" : imp.tone === "warning" ? "▲" : "○";
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${tone}`}>
      <span aria-hidden>{icon}</span>
      {imp.label}
    </span>
  );
}

export function PageTitle({ title, sub }: { title: string; sub?: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h1 className="text-xl font-bold sm:text-2xl">{title}</h1>
      {sub ? <p className="mt-1 text-sm text-muted">{sub}</p> : null}
    </div>
  );
}
