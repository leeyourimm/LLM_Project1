# ADR 0009: 직접 띄우는 관측 도구 (Prometheus·Grafana, OTLP 로 보내는 Langfuse, 개인정보를 지우는 Sentry)

- 상태: 채택
- 관련 코드: `src/dartrag/obs/` (`metrics.py`, `tracing.py`, `errors.py`), `infra/monitoring/`, `infra/docker-compose.yml` 의 `monitoring`·`langfuse` 프로필

## 맥락
- 공개 서비스는 느려지거나 오류가 나면 먼저 알아야 하고, 이상한 답변은 어느 단계(검색, 생성)에서 잘못됐는지 따라가 볼 수 있어야 한다.
- LLM 추적에는 질문과 근거 원문이 그대로 들어간다. 외부 SaaS 로 보내면 "질문을 밖으로 보내지 않는다"는 약속(ADR 0002, 0010)을 깬다.
- 의존성을 늘리고 싶지 않다. Langfuse SDK 는 무겁고 버전 변화가 잦다.

## 결정
- **지표**: `prometheus_client` 로 `/metrics` 를 내보낸다 (요청 수·지연, 답변 단계별 시간, 경고, 토큰 수, 평가, 요청 한도 초과, DB·Redis 상태와 OpenDART 호출 수). 라벨은 경로 대신 라우트 이름을 써서 종류가 끝없이 늘지 않게 한다. 공개 서버에서는 `METRICS_TOKEN` 또는 프록시로 막는다. Prometheus·Grafana 는 docker-compose `monitoring` 프로필로 띄우고 127.0.0.1 에만 연다.
- **LLM 추적**: 직접 띄운 Langfuse 에 SDK 없이 OTLP JSON(`/api/public/otel/v1/traces`)으로 보낸다. 질문 하나가 trace 하나, 그 아래 검색 span 과 생성 generation. 큐에 쌓아 별도 스레드가 묶어 보내므로 Langfuse 가 꺼져도 답변은 나간다. `LANGFUSE_SAMPLE_RATE` 로 일부만 보낼 수 있다.
- **오류 수집**: Sentry 는 `SENTRY_DSN` 이 있을 때만 켠다. `send_default_pii=False`, 지역 변수 수집 끔, 보내기 전에 요청 본문·쿠키·쿼리 문자열·인증 헤더·사용자 정보를 지우고, 메시지 속 이메일, 텔레그램 봇 토큰, 주소의 비밀 쿼리 값(`crtfc_key`, `token` 등)을 가린다.

## 결과와 트레이드오프
- 질문과 근거가 우리 서버 밖으로 나가지 않는다 (Sentry 는 개인정보를 지운 오류만).
- 운영할 것이 많다: Langfuse 는 Postgres·ClickHouse·MinIO·Redis 를 따로 띄우며 메모리를 2~3GB 더 쓴다. 작은 서버에서는 `langfuse` 프로필을 끄고 지표만 쓴다.
- Langfuse 에 쌓인 추적에는 질문과 사용자 번호가 남는다. 탈퇴해도 자동으로 지워지지 않으므로 보관 기간을 Langfuse 쪽에서 따로 정해야 한다 (docs/security-review.md 남은 위험 참고).
- 지표는 프로세스 하나에서 세므로 API 서버는 `--workers 1` 로 띄운다.
