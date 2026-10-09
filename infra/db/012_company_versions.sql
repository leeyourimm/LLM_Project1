-- 회사별 데이터 버전: 재무 수치, 주요 공시, 데이터 검증 결과가 바뀔 때마다 그 회사만 1씩 오른다.
-- 기업 대시보드 캐시 키에 들어가서 바뀐 회사의 미리 만든 응답만 다시 만든다 (src/dartrag/dashboard.py).
-- 피드는 companies 에 없는 회사도 받으므로 외래 키를 걸지 않는다
CREATE TABLE IF NOT EXISTS company_versions (
    corp_code  CHAR(8) PRIMARY KEY,
    version    BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
