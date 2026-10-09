-- 2단계 인증 (TOTP, RFC 6238): 인증 앱 비밀값, 복구 코드, 로그인 중간 단계

-- secret: 서버가 코드를 계산해야 해서 해시로 둘 수 없다. SECRET_KEY 로 만든 키로 암호화(AES-GCM)해
--   두므로 DB 백업만으로는 코드를 만들 수 없다
-- enabled_at: NULL 이면 등록 중(첫 코드 확인 전)이고 로그인에는 쓰지 않는다
-- last_step: 마지막으로 받은 코드의 30초 구간 번호. 같은 코드를 두 번 쓰지 못하게 한다
CREATE TABLE IF NOT EXISTS user_totp (
    user_id    BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    secret     TEXT NOT NULL,
    enabled_at TIMESTAMPTZ,
    last_step  BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 인증 앱을 잃었을 때 쓰는 한 번용 복구 코드. 해시(SHA-256)만 두고 쓰면 지운다
CREATE TABLE IF NOT EXISTS user_recovery_codes (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, code_hash)
);

-- 비밀번호는 맞았고 인증 코드를 기다리는 로그인 (5분, 5번까지 틀릴 수 있음).
-- 토큰 원문은 브라우저 쿠키에만 있고 여기에는 SHA-256 해시만 둔다
CREATE TABLE IF NOT EXISTS login_challenges (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    attempts   SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS login_challenges_user ON login_challenges (user_id);
