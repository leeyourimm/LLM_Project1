-- 작업자(Celery): 새 정기보고서 처리 대기열, 과거 데이터 채우기 진행 상황, 작업 실행 기록, 데이터 검증 결과

-- 피드로 받은 정기보고서(pblntf_ty = 'A')를 원문·재무·청크·색인까지 처리했는지
ALTER TABLE disclosures ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ;
ALTER TABLE disclosures ADD COLUMN IF NOT EXISTS ingest_attempts SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE disclosures ADD COLUMN IF NOT EXISTS ingest_error TEXT;
CREATE INDEX IF NOT EXISTS disclosures_to_ingest ON disclosures (seen_at)
    WHERE pblntf_ty = 'A' AND ingested_at IS NULL;

CREATE TABLE IF NOT EXISTS backfill_state (
    corp_code  CHAR(8) PRIMARY KEY REFERENCES companies(corp_code) ON DELETE CASCADE,
    start_year SMALLINT NOT NULL,
    end_year   SMALLINT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'error')),
    attempts   SMALLINT NOT NULL DEFAULT 0,
    filings    INT NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS backfill_pending ON backfill_state (status, attempts, corp_code);

CREATE TABLE IF NOT EXISTS job_runs (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'error')),
    detail      JSONB
);
CREATE INDEX IF NOT EXISTS job_runs_recent ON job_runs (name, started_at DESC);

-- 재무 데이터 검증 규칙에 걸린 항목. 같은 문제는 한 줄로 유지하고 다시 확인한 시각만 바꾼다
CREATE TABLE IF NOT EXISTS data_issues (
    corp_code   CHAR(8) NOT NULL,
    bsns_year   SMALLINT NOT NULL,
    reprt_code  TEXT NOT NULL,
    fs_div      TEXT NOT NULL,
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('error', 'warn')),
    detail      TEXT NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, rule)
);
