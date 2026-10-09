import type { NextConfig } from "next";

// 개발할 때는 Next(3000)가 /api 요청을 FastAPI(8000)로 넘긴다.
// 배포할 때는 Caddy 가 /api 를 FastAPI 로, 나머지를 Next 로 보낸다.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default config;
