"""Настоящий Postgres: схема, все запросы, логика записи и использование индексов.

Запросы выполняются через psql. Нужна переменная TEST_PG_DSN, без неё тесты пропускаются.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest

from bot.db import queries as q
from bot.db.migrate import migration_files

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(
    not DSN or not shutil.which("psql"), reason="нужны TEST_PG_DSN и psql"
)


class Pg:
    def __init__(self, schema: str) -> None:
        self.schema = schema

    def run(self, sql: str, *, check: bool = True) -> subprocess.CompletedProcess:
        script = f'SET search_path = "{self.schema}";\n{sql}\n'
        return subprocess.run(
            ["psql", DSN, "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"],
            input=script, capture_output=True, text=True, check=check,
        )

    def value(self, sql: str) -> str:
        return self.run(sql).stdout.strip()

    def prepared(self, statement: str, *args: str) -> str:
        """Выполняет запрос из queries.py через PREPARE, как его отправит asyncpg."""
        params = ", ".join(args)
        name = f"q_{uuid.uuid4().hex[:8]}"
        return self.value(f"PREPARE {name} AS {statement};\nEXECUTE {name}({params});")


@pytest.fixture
def pg():
    schema = f"t_{uuid.uuid4().hex[:10]}"
    subprocess.run(
        ["psql", DSN, "-X", "-q", "-c", f'CREATE SCHEMA "{schema}"'],
        check=True, capture_output=True,
    )
    db = Pg(schema)
    for path in migration_files():
        db.run(path.read_text(encoding="utf-8"))
    yield db
    subprocess.run(
        ["psql", DSN, "-X", "-q", "-c", f'DROP SCHEMA "{schema}" CASCADE'],
        check=True, capture_output=True,
    )


ALL_QUERIES = {
    name: value for name, value in vars(q).items() if name.isupper() and isinstance(value, str)
}


@pytest.mark.parametrize("name", sorted(ALL_QUERIES))
def test_query_prepares(pg, name):
    pg.run(f"PREPARE p AS {ALL_QUERIES[name]};")


def _add_bot(pg: Pg, bot_id: int = 1) -> None:
    pg.prepared(q.UPSERT_MAIN_BOT, str(bot_id), "'main'", "'Main'", "100")


def test_only_one_main_bot(pg):
    _add_bot(pg, 1)
    result = pg.run(
        "INSERT INTO bots (id, owner_id, is_main) VALUES (2, 100, TRUE);", check=False
    )
    assert result.returncode != 0
    assert "bots_one_main" in result.stderr


def test_changing_main_token_demotes_old_bot(pg):
    _add_bot(pg, 1)
    pg.prepared(q.DEMOTE_OLD_MAIN, "2")
    _add_bot(pg, 2)
    assert pg.value("SELECT id FROM bots WHERE is_main") == "2"
    assert pg.value("SELECT status FROM bots WHERE id = 1") == "disabled"


def test_upsert_clone_cannot_take_over_main(pg):
    _add_bot(pg, 1)
    out = pg.prepared(q.UPSERT_CLONE, "1", "'x'", "'X'", "555", "'\\x01'::bytea")
    assert out == ""
    assert pg.value("SELECT owner_id FROM bots WHERE id = 1") == "100"


def test_active_clones_list_skips_disabled(pg):
    _add_bot(pg, 1)
    pg.prepared(q.UPSERT_CLONE, "2", "'a'", "'A'", "5", "'\\x01'::bytea")
    pg.prepared(q.UPSERT_CLONE, "3", "'b'", "'B'", "5", "'\\x01'::bytea")
    pg.prepared(q.SET_BOT_STATUS, "3", "'disabled'")
    assert pg.value("SELECT string_agg(id::text, ',') FROM (" + q.LIST_ACTIVE_CLONES + ") s") == "2"


def _upsert_members(pg: Pg, bots, chats, users, counts, when: str = "now()") -> None:
    def arr(values, kind="bigint"):
        return f"ARRAY{list(values)}::{kind}[]"

    pg.prepared(
        q.UPSERT_MEMBERS, arr(bots), arr(chats), arr(users), arr(counts),
        f"ARRAY[{','.join([when] * len(list(users)))}]::timestamptz[]",
    )


def test_upsert_members_adds_counts(pg):
    _upsert_members(pg, [1], [-100], [5], [3])
    _upsert_members(pg, [1, 1], [-100, -100], [5, 6], [2, 1])
    assert pg.value("SELECT msg_count FROM members WHERE user_id = 5") == "5"
    assert pg.value("SELECT msg_count FROM members WHERE user_id = 6") == "1"


def test_last_seen_does_not_go_backwards(pg):
    _upsert_members(pg, [1], [-100], [5], [1], "'2026-02-01'::timestamptz")
    _upsert_members(pg, [1], [-100], [5], [1], "'2026-01-01'::timestamptz")
    assert pg.value("SELECT last_seen::date FROM members") == "2026-02-01"


def test_user_totals_across_chats_of_same_bot_only(pg):
    _upsert_members(pg, [1, 1, 2], [-100, -200, -100], [5, 5, 5], [3, 4, 100])
    assert pg.prepared(q.USER_TOTALS, "1", "5") == "2|7"
    assert pg.prepared(q.USER_TOTALS, "1", "6") == "0|0"


def test_upsert_users_skips_write_when_unchanged(pg):
    args = ("ARRAY[5]::bigint[]", "ARRAY['vasya']::text[]", "ARRAY['Вася']::text[]")
    pg.prepared(q.UPSERT_USERS, *args)
    before = pg.value("SELECT xmin FROM users WHERE id = 5")
    pg.prepared(q.UPSERT_USERS, *args)
    assert pg.value("SELECT xmin FROM users WHERE id = 5") == before
    pg.prepared(
        q.UPSERT_USERS, "ARRAY[5]::bigint[]", "ARRAY['petya']::text[]", "ARRAY['Вася']::text[]"
    )
    assert pg.value("SELECT username FROM users WHERE id = 5") == "petya"


def test_username_goes_to_latest_owner_and_ignores_case(pg):
    pg.run(
        "INSERT INTO users (id, username, first_name, updated_at) VALUES "
        "(1, 'Vasya', 'Старый', '2020-01-01'), (2, 'vasya', 'Новый', '2026-01-01');"
    )
    assert pg.prepared(q.FIND_USER_BY_USERNAME, "'VASYA'").startswith("2|")
    assert pg.prepared(q.FIND_USER_BY_USERNAME, "'nobody'") == ""


def test_chat_settings_merge_and_keep_other_keys(pg):
    _add_bot(pg)
    pg.prepared(q.UPSERT_CHAT, "1", "-100", "'supergroup'", "'Чат'", "NULL")
    pg.prepared(q.MERGE_CHAT_SETTINGS, "1", "-100", """'{"a": 1, "b": 2}'::jsonb""")
    pg.prepared(q.MERGE_CHAT_SETTINGS, "1", "-100", """'{"b": 3}'::jsonb""")
    assert pg.value("SELECT (settings->>'a') || (settings->>'b') FROM chats") == "13"


def test_chat_reactivates_on_upsert(pg):
    _add_bot(pg)
    pg.prepared(q.UPSERT_CHAT, "1", "-100", "'group'", "'Чат'", "NULL")
    pg.prepared(q.SET_CHAT_ACTIVE, "1", "-100", "FALSE")
    assert pg.prepared(q.COUNT_ACTIVE_CHATS, "1") == "0"
    pg.prepared(q.UPSERT_CHAT, "1", "-100", "'group'", "'Чат'", "NULL")
    assert pg.prepared(q.COUNT_ACTIVE_CHATS, "1") == "1"


def test_deleting_bot_removes_its_chats(pg):
    _add_bot(pg)
    pg.prepared(q.UPSERT_CHAT, "1", "-100", "'group'", "'Чат'", "NULL")
    pg.run("DELETE FROM bots WHERE id = 1;")
    assert pg.value("SELECT count(*) FROM chats") == "0"


def test_migrate_moves_settings_and_merges_counts(pg):
    _add_bot(pg)
    pg.prepared(q.UPSERT_CHAT, "1", "-100", "'group'", "'Чат'", "NULL")
    pg.prepared(q.MERGE_CHAT_SETTINGS, "1", "-100", """'{"flood": 5}'::jsonb""")
    _upsert_members(pg, [1, 1], [-100, -100], [5, 6], [10, 1])
    # В новый чат уже успело прийти сообщение раньше служебного события о переезде.
    pg.prepared(q.UPSERT_CHAT, "1", "-200", "'supergroup'", "'Чат'", "NULL")
    _upsert_members(pg, [1], [-200], [5], [2])

    pg.prepared(q.MIGRATE_CHAT, "1", "-100", "-200")
    pg.prepared(q.MIGRATE_MEMBERS, "1", "-100", "-200")

    assert pg.value("SELECT count(*) FROM chats") == "1"
    assert pg.value("SELECT settings->>'flood' FROM chats WHERE chat_id = -200") == "5"
    assert pg.value("SELECT count(*) FROM members WHERE chat_id = -100") == "0"
    assert pg.value("SELECT msg_count FROM members WHERE chat_id = -200 AND user_id = 5") == "12"
    assert pg.value("SELECT msg_count FROM members WHERE chat_id = -200 AND user_id = 6") == "1"


def test_fsm_upsert_delete_and_purge(pg):
    key = ("1", "2", "3", "0", "'default'")
    pg.prepared(q.FSM_UPSERT, *key, "'wait'", """'{"a": 1}'::jsonb""")
    pg.prepared(q.FSM_UPSERT, *key, "'next'", """'{"a": 2}'::jsonb""")
    assert pg.prepared(q.FSM_LOAD, "3").endswith('next|{"a": 2}')
    pg.run("UPDATE fsm_states SET updated_at = now() - interval '10 days';")
    assert pg.prepared(q.FSM_LOAD, "3") == ""
    pg.prepared(q.FSM_PURGE, "3")
    assert pg.value("SELECT count(*) FROM fsm_states") == "0"


def test_counter_columns_are_not_indexed(pg):
    """Если счётчик попадёт в индекс, частые обновления перестанут быть HOT и база разбухнет."""
    indexes = pg.value(
        "SELECT string_agg(indexdef, ' ') FROM pg_indexes "
        "WHERE schemaname = current_schema() AND tablename = 'members'"
    )
    assert "msg_count" not in indexes
    assert "last_seen" not in indexes


@pytest.fixture
def seeded(pg):
    pg.run(
        "INSERT INTO members (bot_id, chat_id, user_id, msg_count) "
        "SELECT 1 + (i % 3), -(i % 500), i % 40000, i % 100 FROM generate_series(1, 200000) i "
        "ON CONFLICT DO NOTHING;"
        "INSERT INTO users (id, username, first_name) "
        "SELECT i, 'user' || i, 'U' FROM generate_series(1, 100000) i;"
        "INSERT INTO bots (id, owner_id) SELECT i, 1 FROM generate_series(1, 3) i;"
        "INSERT INTO chats (bot_id, chat_id, type, is_active) "
        "SELECT 1, -i, 'group', i % 50 = 0 FROM generate_series(1, 5000) i;"
        "ANALYZE;"
    )
    return pg


@pytest.mark.parametrize(
    "statement,args,index",
    [
        (q.USER_TOTALS, ("1", "7"), "members_user"),
        # Планировщик выбирает любой из двух индексов таблицы: оба дают точечный поиск.
        (q.MEMBER_COUNT_IN_CHAT, ("1", "-7", "7"), "members_"),
        (q.FIND_USER_BY_USERNAME, ("'user77'",), "users_username"),
        (q.GET_USER, ("77",), "users_pkey"),
        (q.GET_CHAT, ("1", "-7"), "chats_pkey"),
        (q.COUNT_ACTIVE_CHATS, ("1",), "chats_active"),
    ],
)
def test_hot_queries_use_index(seeded, statement, args, index):
    name = f"e_{uuid.uuid4().hex[:8]}"
    plan = seeded.value(
        f"PREPARE {name} AS {statement};\nEXPLAIN EXECUTE {name}({', '.join(args)});"
    )
    assert index in plan, plan
    assert "Seq Scan" not in plan, plan


# --- Наказания ---


def _punish(pg: Pg, kind: str, *, user: int = 5, reason: str = "'r'", expires: str = "NULL") -> str:
    return pg.prepared(
        q.INSERT_PUNISHMENT, "1", "-100", str(user), f"'{kind}'", "9", reason, expires, "'manual'"
    )


def test_repeated_mute_replaces_active_without_error(pg):
    first = _punish(pg, "mute", reason="'первая'")
    second = _punish(pg, "mute", reason="'вторая'")
    assert first == second
    assert pg.value("SELECT count(*) FROM punishments") == "1"
    assert pg.value("SELECT reason FROM punishments") == "вторая"


def test_ban_and_mute_are_separate_and_warns_stack(pg):
    _punish(pg, "ban")
    _punish(pg, "mute")
    _punish(pg, "warn")
    _punish(pg, "warn")
    assert pg.value("SELECT count(*) FROM punishments") == "4"


def test_new_mute_after_revoke_creates_new_record(pg):
    first = _punish(pg, "mute")
    assert pg.prepared(q.REVOKE_ACTIVE, "1", "-100", "5", "'mute'", "9") == first
    assert _punish(pg, "mute") != first
    assert pg.value("SELECT count(*) FROM punishments") == "2"


def test_warn_count_ignores_revoked_expired_and_other_chats(pg):
    _punish(pg, "warn")
    _punish(pg, "warn", expires="now() - interval '1 day'")
    _punish(pg, "warn", expires="now() + interval '1 day'")
    _punish(pg, "warn", user=6)
    pg.prepared(
        q.INSERT_PUNISHMENT, "1", "-200", "5", "'warn'", "9", "NULL", "NULL", "'manual'"
    )
    assert pg.prepared(q.COUNT_ACTIVE_WARNS, "1", "-100", "5") == "2"


def test_revoke_last_warn_takes_newest_and_stops_when_empty(pg):
    _punish(pg, "warn", reason="'старое'")
    newest = _punish(pg, "warn", reason="'новое'")
    assert pg.prepared(q.REVOKE_LAST_WARN, "1", "-100", "5", "9") == newest
    pg.prepared(q.REVOKE_LAST_WARN, "1", "-100", "5", "9")
    assert pg.prepared(q.REVOKE_LAST_WARN, "1", "-100", "5", "9") == ""
    assert pg.prepared(q.COUNT_ACTIVE_WARNS, "1", "-100", "5") == "0"


def test_revoke_by_id_works_once(pg):
    pid = _punish(pg, "mute")
    assert pg.prepared(q.REVOKE_BY_ID, pid, "9") == pid
    assert pg.prepared(q.REVOKE_BY_ID, pid, "9") == ""


def test_get_punishment_checks_bot_and_chat(pg):
    pid = _punish(pg, "mute")
    assert pg.prepared(q.GET_PUNISHMENT, pid, "1", "-100").startswith(pid)
    assert pg.prepared(q.GET_PUNISHMENT, pid, "2", "-100") == ""
    assert pg.prepared(q.GET_PUNISHMENT, pid, "1", "-200") == ""


def test_history_and_journal_order_and_names(pg):
    pg.run("INSERT INTO users (id, first_name) VALUES (9, 'Админ'), (5, 'Вася');")
    _punish(pg, "warn", reason="'раньше'")
    pg.run("UPDATE punishments SET created_at = now() - interval '1 hour';")
    _punish(pg, "kick", reason="'позже'")
    rows = pg.prepared(q.USER_HISTORY, "1", "-100", "5", "10").splitlines()
    assert rows[0].startswith("kick|позже") and rows[0].endswith("Админ")
    assert rows[1].startswith("warn|раньше")
    assert len(pg.prepared(q.USER_HISTORY, "1", "-100", "5", "1").splitlines()) == 1
    assert "Вася" in pg.prepared(q.CHAT_JOURNAL, "1", "-100", "15").splitlines()[0]


def test_invalid_kind_is_rejected(pg):
    result = pg.run(
        "INSERT INTO punishments (bot_id, chat_id, user_id, kind, admin_id) "
        "VALUES (1, 1, 1, 'расстрел', 1);",
        check=False,
    )
    assert result.returncode != 0


@pytest.fixture
def seeded_punishments(pg):
    pg.run(
        "INSERT INTO punishments (bot_id, chat_id, user_id, kind, admin_id, created_at, revoked_at)"
        " SELECT 1, -(i % 300), i % 20000, 'warn', 9, now() - (i || ' seconds')::interval,"
        " CASE WHEN i % 10 = 0 THEN NULL ELSE now() END FROM generate_series(1, 200000) i;"
        "INSERT INTO users (id, first_name) SELECT i, 'U' FROM generate_series(1, 20000) i;"
        "ANALYZE;"
    )
    return pg


@pytest.mark.parametrize(
    "statement,args",
    [
        (q.COUNT_ACTIVE_WARNS, ("1", "-7", "7")),
        (q.USER_HISTORY, ("1", "-7", "7", "10")),
        (q.CHAT_JOURNAL, ("1", "-7", "15")),
    ],
)
def test_moderation_queries_do_not_scan_table(seeded_punishments, statement, args):
    name = f"e_{uuid.uuid4().hex[:8]}"
    plan = seeded_punishments.value(
        f"PREPARE {name} AS {statement};\nEXPLAIN EXECUTE {name}({', '.join(args)});"
    )
    assert "punishments_" in plan, plan
    assert "Seq Scan on punishments" not in plan, plan
