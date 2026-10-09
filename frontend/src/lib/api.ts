// FastAPI 백엔드 호출. 같은 출처(/api)로 보내 로그인 쿠키가 함께 간다.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    // 응답 본문 (detail 밖의 값이 필요할 때: 2단계 로그인의 restart 등)
    public body: unknown = null,
  ) {
    super(message);
  }
}

function detailOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d[0]?.msg) return "입력값을 확인해 주세요";
  }
  if (status === 401) return "로그인이 필요합니다";
  if (status >= 500) return "서버에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요";
  return `요청 실패 (${status})`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("dartrag:unauthorized"));
    }
    throw new ApiError(res.status, detailOf(body, res.status), body);
  }
  return body as T;
}

export const post = <T>(path: string, data?: unknown) =>
  api<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

export const patch = <T>(path: string, data: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(data) });

export const del = <T>(path: string, data?: unknown) =>
  api<T>(path, { method: "DELETE", body: data === undefined ? undefined : JSON.stringify(data) });

/** 파일 내려받기. 오류(한도 초과 등)는 파일 대신 ApiError 로 알린다. */
export async function download(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    throw new ApiError(res.status, detailOf(body, res.status));
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function qs(params: Record<string, string | number | boolean | string[] | undefined | null>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}
