"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, post } from "@/lib/api";
import type { AuthInfo, Company } from "@/lib/types";

interface AppState {
  auth: AuthInfo | null; // null: 아직 확인 전
  refreshAuth: () => Promise<void>;
  logout: () => Promise<void>;
  companies: Company[];
}

const Ctx = createContext<AppState | null>(null);

export function Providers({ children }: { children: React.ReactNode }) {
  const [auth, setAuth] = useState<AuthInfo | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);

  const refreshAuth = useCallback(async () => {
    try {
      setAuth(await api<AuthInfo>("/api/auth/me"));
    } catch {
      // 서버가 잠시 안 될 때는 로그인 없이 쓰는 것으로 본다
      setAuth({ auth_required: false, allow_signup: false, email_enabled: false, user: null });
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await post("/api/auth/logout");
    } finally {
      await refreshAuth();
    }
  }, [refreshAuth]);

  useEffect(() => {
    refreshAuth();
    const onUnauthorized = () => refreshAuth();
    window.addEventListener("dartrag:unauthorized", onUnauthorized);
    return () => window.removeEventListener("dartrag:unauthorized", onUnauthorized);
  }, [refreshAuth]);

  const signedIn = auth && (!auth.auth_required || auth.user);
  useEffect(() => {
    if (!signedIn) return;
    api<Company[]>("/api/companies").then(setCompanies).catch(() => setCompanies([]));
  }, [signedIn]);

  const value = useMemo(() => ({ auth, refreshAuth, logout, companies }), [auth, refreshAuth, logout, companies]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("Providers 밖에서 useApp 을 썼습니다");
  return v;
}

/** "삼성전자 (005930)", "삼성전자", "005930" → 종목코드 */
export function toStock(text: string, companies: Company[]): string | null {
  const t = text.trim();
  const m = t.match(/(\d{6})\)?$/);
  if (m) return m[1];
  const exact = companies.find((c) => c.corp_name === t);
  if (exact) return exact.stock_code;
  const partial = companies.filter((c) => c.corp_name.includes(t));
  return partial.length === 1 ? partial[0].stock_code : null;
}
