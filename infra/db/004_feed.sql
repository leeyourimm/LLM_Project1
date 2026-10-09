-- 4단계: 주요 공시 피드, 관심 종목, 알림 기록

CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no    CHAR(14) PRIMARY KEY,
    corp_code   CHAR(8) NOT NULL,   -- 피드는 companies 에 없는 회사도 받으므로 FK 없음
    corp_name   TEXT NOT NULL,
    stock_code  TEXT,
    corp_cls    TEXT,               -- Y 유가증권, K 코스닥, N 코넥스, E 기타
    report_nm   TEXT NOT NULL,
    flr_nm      TEXT,
    rcept_dt    DATE NOT NULL,
    rm          TEXT,
    pblntf_ty   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    event_label TEXT NOT NULL,
    importance  SMALLINT NOT NULL,
    correction  BOOLEAN NOT NULL DEFAULT false,
    seen_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS disclosures_recent ON disclosures (rcept_dt DESC, importance DESC);
CREATE INDEX IF NOT EXISTS disclosures_corp ON disclosures (corp_code, rcept_dt DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    corp_code      CHAR(8) PRIMARY KEY,
    min_importance SMALLINT NOT NULL DEFAULT 2 CHECK (min_importance BETWEEN 1 AND 3),
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    rcept_no CHAR(14) NOT NULL REFERENCES disclosures(rcept_no) ON DELETE CASCADE,
    channel  TEXT NOT NULL,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (rcept_no, channel)
);
