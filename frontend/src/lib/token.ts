// 메일 링크(비밀번호 재설정, 이메일 인증)의 ?token= 값.
// 서버가 만드는 토큰은 secrets.token_urlsafe(32) 라 43자 URL 안전 문자뿐이다.
// 모양이 다르면(잘린 링크, 장난) 서버에 보내지 않고 바로 "잘못된 링크"로 보여 준다.
const TOKEN_RE = /^[A-Za-z0-9_-]{20,100}$/;

export function linkToken(raw: string | null | undefined): string | null {
  const t = (raw ?? "").trim();
  return TOKEN_RE.test(t) ? t : null;
}

// 주소창과 방문 기록에서 토큰을 지운다 (공용 PC, 화면 공유, 다른 사이트로 넘어갈 때 대비)
export function hideTokenFromAddressBar(path: string): void {
  if (typeof window === "undefined") return;
  try {
    window.history.replaceState(window.history.state, "", path);
  } catch {
    // 지우지 못해도 토큰은 한 번 쓰면 끝이라 큰 문제는 없다
  }
}
