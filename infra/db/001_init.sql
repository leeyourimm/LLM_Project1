-- 0단계 스키마: 기업, 공시, 재무 수치

CREATE TABLE IF NOT EXISTS companies (
    corp_code        CHAR(8) PRIMARY KEY,
    corp_name        TEXT NOT NULL,
    corp_eng_name    TEXT,
    stock_code       CHAR(6) UNIQUE,
    fiscal_end_month SMALLINT NOT NULL DEFAULT 12 CHECK (fiscal_end_month BETWEEN 1 AND 12),
    modify_date      DATE,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS filings (
    rcept_no      CHAR(14) PRIMARY KEY,
    corp_code     CHAR(8) NOT NULL REFERENCES companies(corp_code),
    report_nm     TEXT NOT NULL,
    report_kind   TEXT,             -- 사업보고서 / 반기보고서 / 분기보고서
    period_key    TEXT,             -- 2024.12
    reprt_code    CHAR(5),
    is_correction BOOLEAN NOT NULL DEFAULT false,
    rcept_dt      DATE NOT NULL,
    raw_key       TEXT,             -- 원문 저장소 키 (받기 전이면 NULL)
    collected_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS filings_corp_period ON filings (corp_code, period_key);

-- 재무제표 계정 한 줄. 금액은 원 단위 정수, 원본 문자열도 함께 보관한다.
CREATE TABLE IF NOT EXISTS financial_items (
    id             BIGSERIAL PRIMARY KEY,
    corp_code      CHAR(8) NOT NULL REFERENCES companies(corp_code),
    bsns_year      SMALLINT NOT NULL,
    reprt_code     CHAR(5) NOT NULL,
    fs_div         CHAR(3) NOT NULL CHECK (fs_div IN ('CFS', 'OFS')),  -- 연결 / 별도
    sj_div         TEXT NOT NULL,     -- BS, IS, CIS, CF, SCE
    account_id     TEXT,              -- IFRS 표준 계정 ID (없으면 회사 자체 계정)
    account_nm     TEXT NOT NULL,
    account_detail TEXT,
    ord            INTEGER,
    amount         BIGINT,            -- 당기 금액 (원)
    add_amount     BIGINT,            -- 당기 누적 금액 (분기·반기 손익)
    currency       TEXT,
    raw_amount     TEXT,
    raw_add_amount TEXT,
    rcept_no       CHAR(14) NOT NULL
);
CREATE INDEX IF NOT EXISTS financial_items_lookup
    ON financial_items (corp_code, account_id, bsns_year, reprt_code, fs_div);
CREATE INDEX IF NOT EXISTS financial_items_report
    ON financial_items (corp_code, bsns_year, reprt_code, fs_div);
