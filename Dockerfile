# syntax=docker/dockerfile:1
# 백엔드 이미지: API(uvicorn), Celery 작업자, beat 가 함께 쓴다.
# 빌드:  docker build -t dartrag-backend .
# 실행은 infra/prod/docker-compose.yml 을 보세요.

ARG PYTHON_VERSION=3.12

# --- 1단계: 패키지 설치 (컴파일 도구와 내려받은 파일은 최종 이미지에 남지 않는다) ---
FROM python:${PYTHON_VERSION}-slim AS builder

# CPU 전용 PyTorch. GPU 판(PyPI 기본)은 수 GB 더 크다. 비우면 PyPI 기본판을 쓴다
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH

RUN python -m venv /opt/venv

WORKDIR /src
# 버전은 requirements/ 잠금 파일(ops+embed)로 고정한다. 갱신 방법은 docs/deploy.md 참고
# 잠금 파일은 x86_64 기준으로 풀었지만 해시에 aarch64 휠도 모두 들어 있고 설치할 패키지도 같아서,
# linux/arm64(예: Oracle Cloud Ampere A1)도 이 파일 그대로 설치한다. CI 의 docker-arm64 작업이 확인한다

# PyTorch 는 크고 자주 바뀌지 않으므로 따로 설치해 빌드 캐시를 살린다.
# 버전만 고정한다(CPU 색인 파일의 해시는 잠금 파일에 없다). 의존 패키지는 아래 잠금 파일에서 받는다
COPY requirements/torch.txt ./requirements/
RUN if [ -n "${TORCH_INDEX_URL}" ]; then \
      pip install --no-deps -r requirements/torch.txt --index-url "${TORCH_INDEX_URL}"; \
    else \
      pip install -r requirements/torch.txt; \
    fi

# 나머지는 해시까지 확인해 설치하고, 우리 패키지는 의존성을 다시 풀지 않고 넣는다
COPY requirements/runtime.txt ./requirements/
RUN pip install --no-deps --require-hashes -r requirements/runtime.txt
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps . && pip check

# --- 2단계: 실행 이미지 ---
FROM python:${PYTHON_VERSION}-slim

# PDF 리포트용 한글 글꼴 (나눔고딕)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# 정기 평가가 eval/manual.jsonl 을 읽는다
COPY --chown=app:app eval ./eval
# data: 원문 저장소, models: 내려받은 임베딩·리랭커 모델 (둘 다 볼륨으로 붙인다)
RUN mkdir -p /app/data/raw /app/models /app/reports \
    && chown -R app:app /app/data /app/models /app/reports

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    REPORT_FONT=/usr/share/fonts/truetype/nanum/NanumGothic.ttf \
    RAW_STORE_DIR=/app/data/raw \
    HF_HOME=/app/models

USER app
EXPOSE 8000

# 지표(/metrics)를 프로세스 하나에서 세므로 --workers 는 1 로 둔다
CMD ["uvicorn", "dartrag.web.asgi:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*", "--workers", "1"]
