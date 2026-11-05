-- Белый список чата: этих людей не трогают антифлуд и антиспам, им можно публиковать рекламу.
-- Читается в память при первом нарушении в чате, а не на каждое сообщение.
CREATE TABLE chat_whitelist (
    bot_id      BIGINT NOT NULL,
    chat_id     BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    added_by    BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, chat_id, user_id)
);
