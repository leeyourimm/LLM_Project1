-- 1단계: 검색 인덱스(Qdrant, OpenSearch) 색인 상태

ALTER TABLE filings ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS index_version SMALLINT;
ALTER TABLE filings ADD COLUMN IF NOT EXISTS index_model TEXT;
