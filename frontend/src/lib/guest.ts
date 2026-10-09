// 가입 없이 체험하기: 체험 계정이 지워지기까지 남은 시간 표시.

/** "23시간 10분", "45분", "1분 미만". 이미 지났으면 "곧". */
export function timeLeft(expiresAt: string | null | undefined, now: Date = new Date()): string {
  if (!expiresAt) return "곧";
  const ms = new Date(expiresAt).getTime() - now.getTime();
  if (!Number.isFinite(ms) || ms <= 0) return "곧";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "1분 미만";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}분`;
  return rest ? `${hours}시간 ${rest}분` : `${hours}시간`;
}

/** 가입 화면 주소. 체험하던 화면으로 돌아오게 next 를 붙인다. */
export function signupHref(pathname: string | null | undefined): string {
  if (!pathname || pathname === "/" || pathname.startsWith("/signup") || pathname.startsWith("/login")) return "/signup";
  return `/signup?next=${encodeURIComponent(pathname)}`;
}
