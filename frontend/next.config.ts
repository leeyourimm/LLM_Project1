import type { NextConfig } from "next";

// 개발할 때는 Next(3000)가 /api 요청을 FastAPI(8000)로 넘긴다.
// 배포할 때는 Caddy 가 /api 를 FastAPI 로, 나머지를 Next 로 보낸다.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

// 화면 응답에 붙이는 보안 헤더. API(/api) 응답은 FastAPI 가 따로 붙인다.
// 스크립트 제한(CSP script-src)은 배포 시 Caddy 가 요청마다 nonce 를 만들어 붙인다 (infra/prod/Caddyfile).
// 여기서는 다른 사이트가 화면을 iframe 으로 감싸는 것(클릭재킹)과 base·form 주소 바꿔치기를 막는다.
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
  },
];

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/((?!api/).*)", headers: SECURITY_HEADERS },
      // 웹 푸시 서비스 워커(public/sw.js): 고친 파일이 바로 반영되게 캐시하지 않는다
      { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }] },
    ];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default config;
