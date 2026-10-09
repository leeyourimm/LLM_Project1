"use client";

import { useEffect, useState } from "react";
import { DisclosureList } from "@/components/DisclosureList";
import { Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api, qs } from "@/lib/api";
import type { Disclosure } from "@/lib/types";

export default function FeedPage() {
  const [days, setDays] = useState(7);
  const [minImportance, setMinImportance] = useState(2);
  const [watchedOnly, setWatchedOnly] = useState(false);
  const [items, setItems] = useState<Disclosure[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    setItems(null);
    setError(null);
    api<Disclosure[]>(`/api/feed${qs({ days, min_importance: minImportance, watched_only: watchedOnly || undefined })}`)
      .then(setItems)
      .catch(setError);
  }, [days, minImportance, watchedOnly, tick]);

  return (
    <div className="space-y-4">
      <PageTitle title="공시 피드" sub="증자, 합병, 소송, 자사주, 실적 같은 주요 공시를 중요도별로 모았습니다." />
      <div className="card flex flex-wrap items-center gap-3 py-3">
        <select className="input w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="기간">
          <option value={1}>오늘</option>
          <option value={7}>최근 7일</option>
          <option value={30}>최근 30일</option>
          <option value={90}>최근 90일</option>
        </select>
        <select
          className="input w-auto"
          value={minImportance}
          onChange={(e) => setMinImportance(Number(e.target.value))}
          aria-label="중요도"
        >
          <option value={3}>매우 중요만</option>
          <option value={2}>중요 이상</option>
          <option value={1}>전체</option>
        </select>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={watchedOnly} onChange={(e) => setWatchedOnly(e.target.checked)} />
          관심 종목만
        </label>
        <button type="button" className="btn ml-auto" onClick={() => setTick((t) => t + 1)}>
          새로고침
        </button>
      </div>
      {error ? (
        <ErrorBox error={error} />
      ) : items === null ? (
        <Loading />
      ) : items.length ? (
        <div className="card py-1">
          <DisclosureList items={items} />
        </div>
      ) : (
        <Empty>이 조건에 맞는 공시가 없습니다.</Empty>
      )}
    </div>
  );
}
