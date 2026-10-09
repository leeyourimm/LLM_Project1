# syntax=docker/dockerfile:1
# 웹 화면 이미지 (Next.js standalone 출력)
# 빌드:  docker build -t dartrag-frontend frontend

ARG NODE_VERSION=22

# --- 1단계: 의존성 설치와 빌드 ---
FROM node:${NODE_VERSION}-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
# /api 요청을 넘길 FastAPI 주소. next.config.ts 의 rewrites 는 빌드할 때 고정되므로 빌드 인자로 받는다.
# 배포에서는 Caddy 가 /api 를 바로 FastAPI 로 보내므로, 이 값은 Next 로 직접 들어온 요청에만 쓰인다
ARG API_ORIGIN=http://api:8000
ENV API_ORIGIN=${API_ORIGIN}
RUN npm run build

# --- 2단계: 실행 이미지 (빌드 도구와 node_modules 전체는 넣지 않는다) ---
FROM node:${NODE_VERSION}-slim
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static

# node 이미지에 들어 있는 일반 사용자
USER node
EXPOSE 3000

CMD ["node", "server.js"]
