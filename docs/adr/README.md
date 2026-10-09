# 의사결정 기록 (ADR)

중요한 기술 선택과 그 이유를 한 파일에 하나씩 남깁니다. 형식은 상태, 맥락, 결정, 결과와 트레이드오프입니다.
결정을 바꾸면 기존 파일을 고치지 말고 새 ADR 을 쓴 뒤, 이전 ADR 의 상태를 "대체됨 (ADR 번호)"로 바꿉니다.

| 번호 | 제목 | 상태 |
|---|---|---|
| [0001](0001-phase0-stack.md) | 0단계 기술 선택 (Python, Postgres 원 단위 저장, 원문 보관) | 채택 |
| [0002](0002-local-llm-ollama.md) | 답변 모델은 Ollama 로컬 모델 | 채택 |
| [0003](0003-hybrid-search.md) | 하이브리드 검색 (Qdrant dense + OpenSearch nori BM25), bge-m3 와 리랭커 | 채택 |
| [0004](0004-grounding-rules.md) | 근거 규칙 (번호 인용, 근거 없는 요약 문장 버리기, 코드 계산, 확인 안 된 숫자 표시) | 채택 |
| [0005](0005-postgres-source-of-truth.md) | Postgres 를 기준 저장소로, 형태가 자주 바뀌는 값은 JSONB | 채택 |
| [0006](0006-celery-redis-workers.md) | Celery + Redis 작업자, 작업 잠금과 OpenDART 호출 한도 공유 | 채택 |
| [0007](0007-rule-based-eval-release-gate.md) | LLM 채점 대신 규칙 채점과 배포 기준 | 채택 |
| [0008](0008-nextjs-with-legacy-static-ui.md) | Next.js 화면을 추가하고 기존 정적 화면도 유지 | 채택 |
| [0009](0009-self-hosted-observability.md) | 직접 띄우는 관측 도구 (Prometheus·Grafana, Langfuse OTLP, Sentry) | 채택 |
| [0010](0010-privacy-first-retention.md) | 개인정보 최소 수집과 보관 기간 | 채택 |

보안 점검 결과는 [docs/security-review.md](../security-review.md)에 있습니다.
