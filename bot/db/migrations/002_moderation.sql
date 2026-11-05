-- Журнал наказаний. Telegram сам снимает временные бан и мут, поэтому планировщик не нужен:
-- в журнале достаточно хранить срок.
CREATE TABLE punishments (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bot_id      BIGINT NOT NULL,
    chat_id     BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('ban', 'mute', 'kick', 'warn')),
    admin_id    BIGINT NOT NULL,                 -- 0: наказал сам бот
    reason      TEXT,
    source      TEXT NOT NULL DEFAULT 'manual',  -- manual, warn_limit
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ,                     -- NULL: навсегда
    revoked_at  TIMESTAMPTZ,
    revoked_by  BIGINT
);

-- История человека в чате.
CREATE INDEX punishments_user ON punishments (bot_id, chat_id, user_id, created_at DESC);
-- Журнал чата.
CREATE INDEX punishments_chat ON punishments (bot_id, chat_id, created_at DESC);
-- Подсчёт предупреждений: в индексе только действующие, поэтому он остаётся маленьким.
CREATE INDEX punishments_active_warns ON punishments (bot_id, chat_id, user_id)
    WHERE kind = 'warn' AND revoked_at IS NULL;
-- У человека в чате не больше одного действующего бана и одного мута.
CREATE UNIQUE INDEX punishments_one_active ON punishments (bot_id, chat_id, user_id, kind)
    WHERE revoked_at IS NULL AND kind IN ('ban', 'mute');
