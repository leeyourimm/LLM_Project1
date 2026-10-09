# ADR 0005: Postgres 를 기준 저장소로, 형태가 자주 바뀌는 값은 JSONB

- 상태: 채택
- 관련 코드: `infra/db/*.sql`, `src/dartrag/db/repository.py`

## 맥락
- 데이터가 여러 곳에 있다: 원문 zip(파일·S3), 재무 수치, 청크, 벡터(Qdrant), 키워드 색인(OpenSearch), 캐시·잠금(Redis), 사용자와 대화 기록.
- 색인이나 캐시가 깨졌을 때 무엇을 기준으로 다시 만들지가 분명해야 한다.
- 답변 기록(출처, 경고, 모델, 걸린 시간), 변경점 요약, 작업 결과, 평가 요약은 기능이 늘 때마다 필드가 바뀐다. 매번 열을 추가하는 마이그레이션은 부담이 크다.

## 결정
- 회사, 공시, 재무 수치(원 단위 BIGINT + 원본 문자열), 청크 원문, 사용자·세션·관심 종목·알림 채널, 대화 기록이 모두 Postgres 에 있다. Qdrant·OpenSearch 는 청크에서 다시 만들 수 있는 색인이고, 색인 상태는 `filings.indexed_at`·`index_version`·`index_model` 로 기록한다.
- 검색·조인·제약이 필요한 값은 일반 열로 두고, 화면에 그대로 돌려주거나 기록용인 값은 JSONB 로 둔다: `messages.payload`, `diff_summaries.payload`, `job_runs.detail`, `eval_runs.meta`·`summary`.
- 스키마는 `infra/db/NNN_*.sql` 을 번호 순서로 `Repository.migrate()` 가 매번 적용한다. 모든 문장은 `IF NOT EXISTS` 로 다시 실행해도 안전하게 쓴다. 별도 마이그레이션 도구는 쓰지 않는다.
- 데이터가 바뀌면 `app_state.data_version` 을 올려 답변 캐시를 무효화한다.
- 사용자를 가리키는 외래 키는 모두 `ON DELETE CASCADE` 로 둔다 (탈퇴 시 남는 기록이 없게, ADR 0010).

## 결과와 트레이드오프
- 백업 대상이 Postgres 와 원문 저장소 둘뿐이다. 색인은 `dartrag index` 로 다시 만든다.
- JSONB 안의 필드는 DB 가 형태를 검사하지 않는다. 읽는 쪽 코드가 예전 형식도 견디게 짜야 한다 (예: 변경점 요약은 `version` 으로 구분).
- 마이그레이션 도구가 없어 열 이름 변경·삭제 같은 되돌리기 어려운 변경은 직접 단계적으로 해야 한다.
- 원문 전문 검색은 Postgres 가 아니라 OpenSearch 가 맡는다.
