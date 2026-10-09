"use client";

// 답변의 [n] ↔ 근거 원문 패널 사이의 상태: 어느 출처가 열렸는지, [n] 으로 열었는지(그러면 패널이 스크롤하고
// 포커스를 옮긴다), 패널에서 답변으로 돌아갈 때 포커스를 돌려줄 [n] 버튼.

import { useRef, useState } from "react";

export function useSourcePanel() {
  const [open, setOpen] = useState<number | null>(null);
  const [request, setRequest] = useState<{ n: number; seq: number } | null>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  const backToAnswer = () => {
    const el = returnTo.current;
    el?.focus();
    return Boolean(el);
  };

  return {
    open,
    request,
    /** 답변의 [n] 을 눌렀을 때 */
    cite(n: number, el: HTMLElement) {
      returnTo.current = el;
      setOpen(n);
      setRequest((r) => ({ n, seq: (r?.seq ?? 0) + 1 }));
    },
    /** 패널에서 출처 제목을 눌렀을 때 */
    toggle(n: number) {
      returnTo.current = null;
      setRequest(null);
      setOpen((cur) => (cur === n ? null : n));
    },
    /** Esc 로 닫을 때. 답변으로 포커스를 돌려줬으면 true */
    close() {
      setOpen(null);
      setRequest(null);
      return backToAnswer();
    },
    onReturn: request ? backToAnswer : null,
  };
}
