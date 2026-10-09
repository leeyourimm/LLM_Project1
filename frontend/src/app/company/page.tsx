"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CompanyInput } from "@/components/CompanyInput";
import { toStock, useApp } from "@/components/providers";
import { ErrorBox, PageTitle } from "@/components/ui";

export default function CompanySearch() {
  const { companies } = useApp();
  const router = useRouter();
  const [text, setText] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const matches = text.trim()
    ? companies.filter((c) => c.corp_name.includes(text.trim()) || c.stock_code.startsWith(text.trim())).slice(0, 12)
    : companies.slice(0, 12);
  return (
    <div>
      <PageTitle title="회사" sub="사업보고서 재무 추이, 분기 실적, 최근 공시, 변경점, PDF 리포트를 봅니다." />
      <form
        className="card flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const stock = toStock(text, companies);
          if (!stock) setError(new Error("회사를 찾지 못했습니다. 목록에서 골라 주세요."));
          else router.push(`/company/${stock}`);
        }}
      >
        <CompanyInput value={text} onChange={setText} required className="min-w-56 flex-1" />
        <button className="btn btn-primary" type="submit">
          보기
        </button>
      </form>
      {error ? (
        <div className="mt-3">
          <ErrorBox error={error} />
        </div>
      ) : null}
      <ul className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {matches.map((c) => (
          <li key={c.corp_code}>
            <Link href={`/company/${c.stock_code}`} className="card block p-3 hover:border-accent">
              <span className="font-medium">{c.corp_name}</span> <span className="text-sm text-muted">{c.stock_code}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
