"""Все SQL-запросы. Тесты подготавливают каждый из них на настоящем Postgres."""

# --- Боты ---

# Токен основного бота сменили на другого бота: старый перестаёт быть основным.
DEMOTE_OLD_MAIN = """
UPDATE bots SET is_main = FALSE, status = 'disabled', updated_at = now()
WHERE is_main AND id <> $1::bigint
"""

UPSERT_MAIN_BOT = """
INSERT INTO bots (id, username, first_name, owner_id, is_main, status)
VALUES ($1::bigint, $2::text, $3::text, $4::bigint, TRUE, 'active')
ON CONFLICT (id) DO UPDATE
SET username = EXCLUDED.username, first_name = EXCLUDED.first_name,
    owner_id = EXCLUDED.owner_id, is_main = TRUE, status = 'active', updated_at = now()
"""

UPSERT_CLONE = """
INSERT INTO bots (id, username, first_name, owner_id, is_main, token_enc, status)
VALUES ($1::bigint, $2::text, $3::text, $4::bigint, FALSE, $5::bytea, 'active')
ON CONFLICT (id) DO UPDATE
SET username = EXCLUDED.username, first_name = EXCLUDED.first_name,
    owner_id = EXCLUDED.owner_id, token_enc = EXCLUDED.token_enc,
    status = 'active', updated_at = now()
WHERE NOT bots.is_main
RETURNING id
"""

LIST_ACTIVE_CLONES = """
SELECT id, username, owner_id, token_enc FROM bots
WHERE NOT is_main AND status = 'active'
"""

LIST_OWNER_CLONES = """
SELECT id, username, first_name, status FROM bots
WHERE owner_id = $1::bigint AND NOT is_main
ORDER BY created_at
"""

SET_BOT_STATUS = """
UPDATE bots SET status = $2::text, updated_at = now() WHERE id = $1::bigint AND NOT is_main
"""

COUNT_BOTS = """
SELECT count(*) FILTER (WHERE status = 'active') AS active, count(*) AS total FROM bots
"""

# --- Люди ---

UPSERT_USERS = """
INSERT INTO users (id, username, first_name)
SELECT * FROM UNNEST($1::bigint[], $2::text[], $3::text[])
ON CONFLICT (id) DO UPDATE
SET username = EXCLUDED.username, first_name = EXCLUDED.first_name, updated_at = now()
WHERE users.username IS DISTINCT FROM EXCLUDED.username
   OR users.first_name IS DISTINCT FROM EXCLUDED.first_name
"""

GET_USER = "SELECT id, username, first_name FROM users WHERE id = $1::bigint"

FIND_USER_BY_USERNAME = """
SELECT id, username, first_name FROM users
WHERE lower(username) = lower($1::text) AND username IS NOT NULL
ORDER BY updated_at DESC
LIMIT 1
"""

# --- Чаты ---

GET_CHAT = """
SELECT type, title, username, is_active, settings FROM chats
WHERE bot_id = $1::bigint AND chat_id = $2::bigint
"""

UPSERT_CHAT = """
INSERT INTO chats (bot_id, chat_id, type, title, username)
VALUES ($1::bigint, $2::bigint, $3::text, $4::text, $5::text)
ON CONFLICT (bot_id, chat_id) DO UPDATE
SET type = EXCLUDED.type, title = EXCLUDED.title, username = EXCLUDED.username,
    is_active = TRUE, updated_at = now()
"""

SET_CHAT_ACTIVE = """
UPDATE chats SET is_active = $3::boolean, updated_at = now()
WHERE bot_id = $1::bigint AND chat_id = $2::bigint
"""

MERGE_CHAT_SETTINGS = """
UPDATE chats SET settings = settings || $3::jsonb, updated_at = now()
WHERE bot_id = $1::bigint AND chat_id = $2::bigint
"""

COUNT_ACTIVE_CHATS = """
SELECT count(*) FROM chats WHERE bot_id = $1::bigint AND is_active
"""

# Группа стала супергруппой: у неё новый id, настройки и счётчики переезжают.
MIGRATE_CHAT = """
WITH moved AS (
    DELETE FROM chats WHERE bot_id = $1::bigint AND chat_id = $2::bigint
    RETURNING title, username, settings, added_at
)
INSERT INTO chats (bot_id, chat_id, type, title, username, settings, added_at)
SELECT $1::bigint, $3::bigint, 'supergroup', title, username, settings, added_at FROM moved
ON CONFLICT (bot_id, chat_id) DO UPDATE
SET settings = EXCLUDED.settings, added_at = EXCLUDED.added_at, is_active = TRUE, updated_at = now()
"""

MIGRATE_MEMBERS = """
WITH moved AS (
    DELETE FROM members WHERE bot_id = $1::bigint AND chat_id = $2::bigint
    RETURNING user_id, msg_count, first_seen, last_seen
)
INSERT INTO members (bot_id, chat_id, user_id, msg_count, first_seen, last_seen)
SELECT $1::bigint, $3::bigint, user_id, msg_count, first_seen, last_seen FROM moved
ON CONFLICT (bot_id, chat_id, user_id) DO UPDATE
SET msg_count = members.msg_count + EXCLUDED.msg_count,
    first_seen = LEAST(members.first_seen, EXCLUDED.first_seen),
    last_seen = GREATEST(members.last_seen, EXCLUDED.last_seen)
"""

# --- Статистика ---

UPSERT_MEMBERS = """
INSERT INTO members (bot_id, chat_id, user_id, msg_count, first_seen, last_seen)
SELECT b, c, u, n, t, t
FROM UNNEST($1::bigint[], $2::bigint[], $3::bigint[], $4::bigint[], $5::timestamptz[])
     AS x(b, c, u, n, t)
ON CONFLICT (bot_id, chat_id, user_id) DO UPDATE
SET msg_count = members.msg_count + EXCLUDED.msg_count,
    last_seen = GREATEST(members.last_seen, EXCLUDED.last_seen)
"""

MEMBER_COUNT_IN_CHAT = """
SELECT msg_count FROM members
WHERE bot_id = $1::bigint AND chat_id = $2::bigint AND user_id = $3::bigint
"""

# Активность человека по всем чатам бота, где он отмечался.
USER_TOTALS = """
SELECT count(*) AS chats, COALESCE(sum(msg_count), 0)::bigint AS messages
FROM members
WHERE bot_id = $1::bigint AND user_id = $2::bigint
"""

# --- Незаконченные диалоги ---

FSM_LOAD = """
SELECT bot_id, chat_id, user_id, thread_id, destiny, state, data FROM fsm_states
WHERE updated_at > now() - make_interval(days => $1::int)
"""

FSM_PURGE = "DELETE FROM fsm_states WHERE updated_at <= now() - make_interval(days => $1::int)"

FSM_UPSERT = """
INSERT INTO fsm_states (bot_id, chat_id, user_id, thread_id, destiny, state, data)
VALUES ($1::bigint, $2::bigint, $3::bigint, $4::bigint, $5::text, $6::text, $7::jsonb)
ON CONFLICT (bot_id, chat_id, user_id, thread_id, destiny) DO UPDATE
SET state = EXCLUDED.state, data = EXCLUDED.data, updated_at = now()
"""

FSM_DELETE = """
DELETE FROM fsm_states
WHERE bot_id = $1::bigint AND chat_id = $2::bigint AND user_id = $3::bigint
  AND thread_id = $4::bigint AND destiny = $5::text
"""

# --- Наказания ---

# Повторный бан или мут заменяет прежний. ON CONFLICT закрывает гонку двух админов.
INSERT_PUNISHMENT = """
INSERT INTO punishments (bot_id, chat_id, user_id, kind, admin_id, reason, expires_at, source)
VALUES ($1::bigint, $2::bigint, $3::bigint, $4::text, $5::bigint, $6::text,
        $7::timestamptz, $8::text)
ON CONFLICT (bot_id, chat_id, user_id, kind) WHERE revoked_at IS NULL AND kind IN ('ban', 'mute')
DO UPDATE SET admin_id = EXCLUDED.admin_id, reason = EXCLUDED.reason,
    expires_at = EXCLUDED.expires_at, source = EXCLUDED.source, created_at = now()
RETURNING id
"""

REVOKE_ACTIVE = """
UPDATE punishments SET revoked_at = now(), revoked_by = $5::bigint
WHERE bot_id = $1::bigint AND chat_id = $2::bigint AND user_id = $3::bigint
  AND kind = $4::text AND revoked_at IS NULL
RETURNING id
"""

COUNT_ACTIVE_WARNS = """
SELECT count(*) FROM punishments
WHERE bot_id = $1::bigint AND chat_id = $2::bigint AND user_id = $3::bigint
  AND kind = 'warn' AND revoked_at IS NULL
  AND (expires_at IS NULL OR expires_at > now())
"""

REVOKE_LAST_WARN = """
UPDATE punishments SET revoked_at = now(), revoked_by = $4::bigint
WHERE id = (
    SELECT id FROM punishments
    WHERE bot_id = $1::bigint AND chat_id = $2::bigint AND user_id = $3::bigint
      AND kind = 'warn' AND revoked_at IS NULL
      AND (expires_at IS NULL OR expires_at > now())
    ORDER BY id DESC
    LIMIT 1
)
RETURNING id
"""

GET_PUNISHMENT = """
SELECT p.id, p.user_id, p.kind, p.expires_at, p.revoked_at, u.first_name, u.username
FROM punishments p
LEFT JOIN users u ON u.id = p.user_id
WHERE p.id = $1::bigint AND p.bot_id = $2::bigint AND p.chat_id = $3::bigint
"""

# Атомарно: при двойном нажатии кнопки снимет только первое.
REVOKE_BY_ID = """
UPDATE punishments SET revoked_at = now(), revoked_by = $2::bigint
WHERE id = $1::bigint AND revoked_at IS NULL
RETURNING id
"""

USER_HISTORY = """
SELECT p.kind, p.reason, p.created_at, p.expires_at, p.revoked_at,
       a.first_name AS admin_name, a.username AS admin_username
FROM punishments p
LEFT JOIN users a ON a.id = p.admin_id
WHERE p.bot_id = $1::bigint AND p.chat_id = $2::bigint AND p.user_id = $3::bigint
ORDER BY p.created_at DESC
LIMIT $4::int
"""

CHAT_JOURNAL = """
SELECT p.kind, p.user_id, p.reason, p.created_at, p.expires_at, p.revoked_at,
       t.first_name AS target_name, t.username AS target_username,
       a.first_name AS admin_name, a.username AS admin_username
FROM punishments p
LEFT JOIN users t ON t.id = p.user_id
LEFT JOIN users a ON a.id = p.admin_id
WHERE p.bot_id = $1::bigint AND p.chat_id = $2::bigint
ORDER BY p.created_at DESC
LIMIT $3::int
"""

# --- Белый список ---

WHITELIST_ADD = """
INSERT INTO chat_whitelist (bot_id, chat_id, user_id, added_by)
VALUES ($1::bigint, $2::bigint, $3::bigint, $4::bigint)
ON CONFLICT (bot_id, chat_id, user_id) DO NOTHING
RETURNING user_id
"""

WHITELIST_REMOVE = """
DELETE FROM chat_whitelist
WHERE bot_id = $1::bigint AND chat_id = $2::bigint AND user_id = $3::bigint
RETURNING user_id
"""

WHITELIST_IDS = """
SELECT user_id FROM chat_whitelist WHERE bot_id = $1::bigint AND chat_id = $2::bigint
"""

WHITELIST_LIST = """
SELECT w.user_id, u.first_name, u.username
FROM chat_whitelist w
LEFT JOIN users u ON u.id = w.user_id
WHERE w.bot_id = $1::bigint AND w.chat_id = $2::bigint
ORDER BY w.created_at
LIMIT $3::int
"""

# --- Переезд группы в супергруппу: журнал и белый список ---

MIGRATE_PUNISHMENTS = """
UPDATE punishments p SET chat_id = $3::bigint
WHERE p.bot_id = $1::bigint AND p.chat_id = $2::bigint
  AND NOT (
    p.revoked_at IS NULL AND p.kind IN ('ban', 'mute') AND EXISTS (
      SELECT 1 FROM punishments n
      WHERE n.bot_id = p.bot_id AND n.chat_id = $3::bigint AND n.user_id = p.user_id
        AND n.kind = p.kind AND n.revoked_at IS NULL
    )
  )
"""

MIGRATE_WHITELIST = """
WITH moved AS (
    DELETE FROM chat_whitelist WHERE bot_id = $1::bigint AND chat_id = $2::bigint
    RETURNING user_id, added_by, created_at
)
INSERT INTO chat_whitelist (bot_id, chat_id, user_id, added_by, created_at)
SELECT $1::bigint, $3::bigint, user_id, added_by, created_at FROM moved
ON CONFLICT (bot_id, chat_id, user_id) DO NOTHING
"""
