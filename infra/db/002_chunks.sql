-- 1단계: 공시 원문 청크

ALTER TABLE filings ADD COLUMN IF NOT EXISTS parsed_at TIMESTAMPTZ;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS parser_version SMALLINT;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    rcept_no     CHAR(14) NOT NULL REFERENCES filings(rcept_no) ON DELETE CASCADE,
    corp_code    CHAR(8) NOT NULL REFERENCES companies(corp_code),
    source_file  TEXT NOT NULL,      -- 본문 또는 첨부 XML 파일명
    ord          INTEGER NOT NULL,   -- 파일 안 순서
    kind         TEXT NOT NULL CHECK (kind IN ('text', 'table')),
    section_path TEXT[] NOT NULL,
    context      TEXT NOT NULL,      -- 검색용 맥락 문장
    body         TEXT NOT NULL,      -- 원문 텍스트 (표는 마크다운)
    unit         TEXT,
    char_count   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_filing ON chunks (rcept_no, source_file, ord);
CREATE INDEX IF NOT EXISTS chunks_corp ON chunks (corp_code);
