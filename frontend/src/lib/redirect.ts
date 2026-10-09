// 로그인 뒤 돌아갈 주소(?next=). 이 사이트 안의 경로만 받는다.
// "//evil.com", "/\evil.com"(브라우저가 //evil.com 으로 읽음), 제어 문자가 든 값은 첫 화면으로 보낸다.
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/")) return "/";
  if (next.startsWith("//") || next.includes("\\")) return "/";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(next)) return "/";
  return next;
}
