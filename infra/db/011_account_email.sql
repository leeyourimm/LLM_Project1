-- 계정 메일: 이메일 인증, 비밀번호 재설정, 새 기기 로그인 알림

-- 가입한 이메일의 소유를 확인한 시각. NULL 이면 아직 인증 전
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

-- 이메일 알림 인증 링크를 이미 연 사용자는 같은 주소를 확인한 것이라 인증된 것으로 본다
UPDATE users u SET email_verified_at = c.verified_at
FROM user_alert_channels c
WHERE c.user_id = u.id AND c.kind = 'email' AND c.target = u.email
  AND c.verified_at IS NOT NULL AND u.email_verified_at IS NULL;

-- 메일로 보내는 한 번 쓰는 링크 (비밀번호 재설정 30분, 이메일 인증 24시간).
-- 토큰 원문은 메일에만 있고 여기에는 SHA-256 해시만 둔다. 쓰면 바로 지운다
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose    TEXT NOT NULL CHECK (purpose IN ('reset', 'verify')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_tokens_user ON auth_tokens (user_id, purpose);

-- 로그인한 적 있는 기기 (브라우저 종류·OS·접속 네트워크). 원래 값은 두지 않고
-- SECRET_KEY 로 만든 HMAC 만 둔다. 처음 보는 조합이면 새 기기 로그인 알림을 보낸다
CREATE TABLE IF NOT EXISTS user_devices (
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_hash CHAR(64) NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, device_hash)
);
