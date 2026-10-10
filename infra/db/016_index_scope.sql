-- 자동 색인에 운영자가 더한 회사 (INDEX_SCOPE=focus 일 때, dartrag scope add)
--
-- focus 에서는 기본 15개사(config.DEFAULT_STOCKS)와 이 표의 회사만 새 정기보고서를 받아
-- 원문·청크·검색 색인까지 만든다. 공시 피드와 알림은 어느 쪽이든 모든 상장사가 대상이다.
-- 회사 목록(companies)을 아직 받지 않았어도 넣을 수 있게 종목코드로 저장한다
CREATE TABLE IF NOT EXISTS index_scope (
    stock_code CHAR(6) PRIMARY KEY,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
