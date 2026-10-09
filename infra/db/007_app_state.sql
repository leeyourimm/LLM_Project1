-- 서비스 상태 값 (예: data_version 은 검색·재무 데이터가 바뀔 때마다 1씩 올라 답변 캐시를 무효화)
CREATE TABLE IF NOT EXISTS app_state (
    key        TEXT PRIMARY KEY,
    value      BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
