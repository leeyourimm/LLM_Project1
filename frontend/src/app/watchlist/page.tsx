"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { CompanyInput } from "@/components/CompanyInput";
import { toStock, useApp } from "@/components/providers";
import { Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api, del, post } from "@/lib/api";
import { IMPORTANCE, type WatchItem } from "@/lib/types";
import { AlertSettings } from "./AlertSettings";

export default function WatchlistPage() {
  const { companies } = useApp();
  const [items, setItems] = useState<WatchItem[] | null>(null);
  const [text, setText] = useState("");
  const [level, setLevel] = useState(2);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback(() => {
    api<WatchItem[]>("/api/watchlist").then(setItems).catch(setError);
  }, []);
  useEffect(load, [load]);

  return (
    <div className="space-y-4">
      <PageTitle title="관심 종목" sub="관심 종목에 주요 공시가 올라오면 알림을 보냅니다." />
      <form
        className="card flex flex-wrap items-end gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          const stock = toStock(text, companies);
          if (!stock) {
            setError(new Error("회사를 찾지 못했습니다. 목록에서 골라 주세요."));
            return;
          }
          try {
            await post("/api/watchlist", { stock, min_importance: level });
            setText("");
            load();
          } catch (err) {
            setError(err);
          }
        }}
      >
        <CompanyInput value={text} onChange={setText} required className="min-w-56 flex-1" />
        <select className="input w-auto" value={level} onChange={(e) => setLevel(Number(e.target.value))} aria-label="알림 기준">
          <option value={3}>매우 중요한 공시만</option>
          <option value={2}>중요 이상 공시</option>
          <option value={1}>모든 공시</option>
        </select>
        <button className="btn btn-primary" type="submit">
          추가
        </button>
      </form>
      {error ? <ErrorBox error={error} /> : null}
      {items === null ? (
        <Loading />
      ) : items.length ? (
        <ul className="card divide-y divide-line py-1">
          {items.map((w) => (
            <li key={w.corp_code} className="flex items-center gap-3 py-2.5 text-sm">
              <Link href={`/company/${w.stock_code}`} className="font-medium hover:underline">
                {w.corp_name}
              </Link>
              <span className="text-muted">
                {w.stock_code} · {IMPORTANCE[w.min_importance]?.label} 이상 알림
              </span>
              <span className="flex-1" />
              <button
                type="button"
                className="btn px-2 py-1 text-xs"
                onClick={async () => {
                  try {
                    await del(`/api/watchlist/${w.stock_code}`);
                    load();
                  } catch (err) {
                    setError(err);
                  }
                }}
              >
                삭제
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>관심 종목이 없습니다.</Empty>
      )}
      <AlertSettings />
    </div>
  );
}
