import type { Disclosure } from "@/lib/types";
import { ImportanceBadge } from "./ui";

export function DisclosureList({ items, showCompany = true }: { items: Disclosure[]; showCompany?: boolean }) {
  return (
    <ul className="divide-y divide-line">
      {items.map((d) => (
        <li key={d.rcept_no} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-sm">
          <ImportanceBadge level={d.importance} />
          <span className="w-20 shrink-0 text-xs text-muted tabular-nums">{String(d.rcept_dt).slice(0, 10)}</span>
          {showCompany ? <span className="font-medium">{d.corp_name}</span> : null}
          <span className="text-muted">
            {d.event_label}
            {d.correction ? " (정정)" : ""}
          </span>
          <a href={d.url} target="_blank" rel="noopener noreferrer" className="link min-w-0 flex-1 truncate">
            {d.report_nm}
          </a>
        </li>
      ))}
    </ul>
  );
}
