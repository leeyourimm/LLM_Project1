-- 웹 푸시 알림: 브라우저마다 받은 구독(PushSubscription)을 사용자별로 둔다

-- 알림 채널 종류에 push 를 더한다. 켜기·끄기는 다른 채널처럼 user_alert_channels 의 한 줄로 하고
-- (target 은 비움), 받는 곳은 아래 구독 표에 브라우저마다 한 줄씩 둔다
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'user_alert_channels_kind_check'
          AND conrelid = 'user_alert_channels'::regclass
          AND position('push' in pg_get_constraintdef(oid)) > 0
    ) THEN
        ALTER TABLE user_alert_channels DROP CONSTRAINT IF EXISTS user_alert_channels_kind_check;
        ALTER TABLE user_alert_channels ADD CONSTRAINT user_alert_channels_kind_check
            CHECK (kind IN ('email', 'telegram', 'push'));
    END IF;
END $$;

-- endpoint: 브라우저 회사의 푸시 서비스가 준 주소. 이 주소와 키만 있으면 그 브라우저로 알림을 보낼 수
--   있으므로 로그와 내보내기에 넣지 않는다 (내보내기에는 푸시 서비스 호스트만)
-- p256dh, auth: 알림 내용을 그 브라우저만 풀 수 있게 암호화하는 브라우저 키 (RFC 8291)
-- vapid_key: 구독할 때 쓴 서버 공개키. 서버 키를 바꾸면 예전 구독으로는 보낼 수 없어 지운다
-- label: 화면에 보여 줄 "브라우저 · OS" (User-Agent 원문은 두지 않는다)
CREATE TABLE IF NOT EXISTS user_push_subscriptions (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint     TEXT NOT NULL UNIQUE,
    p256dh       TEXT NOT NULL,
    auth         TEXT NOT NULL,
    vapid_key    TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS user_push_subscriptions_user ON user_push_subscriptions (user_id);
