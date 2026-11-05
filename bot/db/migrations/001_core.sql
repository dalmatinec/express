-- Ядро. Все данные чатов живут в разрезе bot_id: основной бот и каждый клон
-- не видят чужие чаты и статистику. Настройки хранятся у пары (бот, чат).

CREATE TABLE bots (
    id          BIGINT PRIMARY KEY,               -- id из токена Telegram
    username    TEXT,
    first_name  TEXT,
    owner_id    BIGINT NOT NULL,
    is_main     BOOLEAN NOT NULL DEFAULT FALSE,
    token_enc   BYTEA,                            -- зашифрован; у основного бота NULL, токен в .env
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'disabled', 'revoked')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Основной бот может быть только один.
CREATE UNIQUE INDEX bots_one_main ON bots ((TRUE)) WHERE is_main;
-- «Мои клоны» у владельца.
CREATE INDEX bots_owner ON bots (owner_id) WHERE NOT is_main;

-- Общий справочник людей: нужен, чтобы находить цель по @username.
CREATE TABLE users (
    id          BIGINT PRIMARY KEY,
    username    TEXT,
    first_name  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
) WITH (fillfactor = 90);
-- Username может перейти к другому человеку, поэтому берём самую свежую запись.
CREATE INDEX users_username ON users (lower(username), updated_at DESC)
    WHERE username IS NOT NULL;

CREATE TABLE chats (
    bot_id      BIGINT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    chat_id     BIGINT NOT NULL,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    username    TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    settings    JSONB NOT NULL DEFAULT '{}'::jsonb,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, chat_id)
);
-- Список чатов бота и их подсчёт: в индекс попадают только живые чаты.
CREATE INDEX chats_active ON chats (bot_id, added_at DESC) WHERE is_active;

-- Самая нагруженная таблица: счётчик обновляется постоянно.
-- fillfactor оставляет место на странице, а msg_count и last_seen намеренно не входят
-- в индексы. Так обновления остаются HOT: индексы не трогаются и почти не пухнут.
-- Внешних ключей нет сознательно: они замедляли бы каждую пачку записи.
CREATE TABLE members (
    bot_id      BIGINT NOT NULL,
    chat_id     BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    msg_count   BIGINT NOT NULL DEFAULT 0,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, chat_id, user_id)
) WITH (
    fillfactor = 70,
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.05
);
-- Профиль человека по всем чатам бота, включая партнёрские.
CREATE INDEX members_user ON members (bot_id, user_id);

-- Незаконченные диалоги настройки, чтобы они переживали перезапуск.
-- Читаются один раз при старте, дальше обслуживаются из памяти.
CREATE TABLE fsm_states (
    bot_id      BIGINT NOT NULL,
    chat_id     BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    thread_id   BIGINT NOT NULL DEFAULT 0,
    destiny     TEXT NOT NULL DEFAULT 'default',
    state       TEXT,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, chat_id, user_id, thread_id, destiny)
);
CREATE INDEX fsm_states_updated ON fsm_states (updated_at);
