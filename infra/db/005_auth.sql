-- 5단계: 로그인 (공개 배포용). 내 컴퓨터에서만 쓸 때는 쓰지 않는다.

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,      -- 소문자로 저장
    password_hash TEXT NOT NULL,             -- scrypt, 평문은 저장하지 않음
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

-- 세션 토큰은 해시만 저장해서 DB가 유출돼도 로그인 상태를 훔칠 수 없게 한다
CREATE TABLE IF NOT EXISTS sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id);

-- 사용자별 관심 종목. 기존 watchlist 는 운영자(CLI·알림)용으로 그대로 둔다
CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corp_code      CHAR(8) NOT NULL,
    min_importance SMALLINT NOT NULL DEFAULT 2 CHECK (min_importance BETWEEN 1 AND 3),
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, corp_code)
);
