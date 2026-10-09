-- 평가 실행 기록: 정기 평가 결과를 쌓아 품질 추이를 대시보드에 보여 준다
CREATE TABLE IF NOT EXISTS eval_runs (
    id          BIGSERIAL PRIMARY KEY,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta        JSONB NOT NULL,
    summary     JSONB NOT NULL,
    passed      BOOLEAN NOT NULL  -- 배포 기준을 모두 넘었는지
);
CREATE INDEX IF NOT EXISTS eval_runs_recent ON eval_runs (finished_at DESC);
