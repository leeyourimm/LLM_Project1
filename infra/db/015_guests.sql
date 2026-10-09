-- 가입 없이 체험하기 (ALLOW_GUEST=true): 이메일·비밀번호 없는 체험 계정

-- 체험 계정은 이메일과 비밀번호가 없다. 이메일 UNIQUE 는 NULL 끼리는 겹쳐도 된다
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- 체험 계정이 끝나는 시각. NULL 이면 가입한 계정이다. 지나면 로그인이 끊기고, 작업자가
-- 탈퇴와 같은 경로로 기록과 함께 지운다. 체험 중에 가입하면 NULL 이 되어 기록이 그대로 남는다
ALTER TABLE users ADD COLUMN IF NOT EXISTS guest_expires_at TIMESTAMPTZ;

-- 가입한 계정(체험 계정이 아닌 것)에는 이메일과 비밀번호가 반드시 있다
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_member_has_login' AND conrelid = 'users'::regclass
    ) THEN
        ALTER TABLE users ADD CONSTRAINT users_member_has_login
            CHECK (guest_expires_at IS NOT NULL OR (email IS NOT NULL AND password_hash IS NOT NULL));
    END IF;
END $$;

-- 기한이 지난 체험 계정 찾기용
CREATE INDEX IF NOT EXISTS users_guest_expires ON users (guest_expires_at)
    WHERE guest_expires_at IS NOT NULL;
