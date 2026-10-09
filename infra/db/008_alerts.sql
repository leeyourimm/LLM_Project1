-- 변경점 요약 저장, 사용자별 알림 채널(이메일·텔레그램)과 발송 기록

CREATE TABLE IF NOT EXISTS diff_summaries (
    old_rcept_no CHAR(14) NOT NULL,
    new_rcept_no CHAR(14) NOT NULL,
    version      INT NOT NULL,
    model        TEXT,                 -- NULL 이면 LLM 없이 만든 요약
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (old_rcept_no, new_rcept_no)
);
CREATE INDEX IF NOT EXISTS diff_summaries_new ON diff_summaries (new_rcept_no);

-- kind: email / telegram. target: 이메일 주소 또는 텔레그램 chat_id
-- verified_at 이 있어야 보낸다 (남의 주소로 알림을 보내지 못하게)
CREATE TABLE IF NOT EXISTS user_alert_channels (
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('email', 'telegram')),
    target       TEXT,
    verified_at  TIMESTAMPTZ,
    enabled      BOOLEAN NOT NULL DEFAULT true,
    -- 인증 링크·텔레그램 연결 코드. 해시만 저장한다
    pending_hash CHAR(64),
    pending_until TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind)
);
CREATE INDEX IF NOT EXISTS user_alert_channels_pending ON user_alert_channels (pending_hash);

CREATE TABLE IF NOT EXISTS user_notifications (
    user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rcept_no CHAR(14) NOT NULL REFERENCES disclosures(rcept_no) ON DELETE CASCADE,
    channel  TEXT NOT NULL,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, rcept_no, channel)
);
