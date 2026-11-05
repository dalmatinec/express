from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bot.admins import AdminCache, rights_from_member
from bot.cache import TTLCache
from bot.chats import ChatRepo
from bot.config import ConfigError, Settings, load_dotenv
from bot.crypto import TokenCipher
from bot.db import queries as q
from bot.db.migrate import migrate, migration_files
from bot.fsm_store import FsmStore
from bot.metrics import is_countable, rss_mb
from bot.polling import ErrorAction, PollLoop
from bot.stats import StatsBuffer
from bot.texts import t

from .fakes import FakeDb

# --- Кэш ---


def test_cache_expires_and_evicts_oldest():
    now = [0.0]
    cache: TTLCache[str, int] = TTLCache(2, 10, clock=lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # «a» теперь свежее «b»
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    now[0] = 11
    assert cache.get("a") is None
    assert cache.misses == 2


def test_cache_stores_none_as_value():
    async def scenario():
        cache: TTLCache[str, None] = TTLCache(5, 10)
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            return None

        assert await cache.get_or_load("x", load) is None
        assert await cache.get_or_load("x", load) is None
        assert calls == 1

    asyncio.run(scenario())


def test_cache_concurrent_misses_load_once():
    async def scenario():
        cache: TTLCache[str, int] = TTLCache(5, 10)
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return 42

        results = await asyncio.gather(*(cache.get_or_load("k", load) for _ in range(50)))
        assert results == [42] * 50
        assert calls == 1

    asyncio.run(scenario())


def test_cache_invalidation_during_load_is_not_stored():
    async def scenario():
        cache: TTLCache[str, str] = TTLCache(5, 10)
        started, release = asyncio.Event(), asyncio.Event()

        async def load():
            started.set()
            await release.wait()
            return "старое"

        waiter = asyncio.create_task(cache.get_or_load("k", load))
        await started.wait()
        cache.delete("k")  # запись в базу прошла, пока шла загрузка
        release.set()
        assert await waiter == "старое"
        assert cache.get("k") is None

    asyncio.run(scenario())


def test_cache_loader_error_is_not_cached():
    async def scenario():
        cache: TTLCache[str, int] = TTLCache(5, 10)

        async def bad():
            raise ValueError("сбой")

        async def good():
            return 7

        with pytest.raises(ValueError):
            await cache.get_or_load("k", bad)
        assert await cache.get_or_load("k", good) == 7

    asyncio.run(scenario())


def test_cache_cancelled_waiter_does_not_break_others():
    async def scenario():
        cache: TTLCache[str, int] = TTLCache(5, 10)
        release = asyncio.Event()

        async def load():
            await release.wait()
            return 1

        first = asyncio.create_task(cache.get_or_load("k", load))
        second = asyncio.create_task(cache.get_or_load("k", load))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        release.set()
        assert await second == 1

    asyncio.run(scenario())


# --- Статистика ---


def test_stats_collapse_messages_into_one_row():
    async def scenario():
        db = FakeDb()
        stats = StatsBuffer(db)
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(1000):
            stats.add_message(1, -100, 5, base + timedelta(seconds=i))
        stats.add_message(1, -100, 6, base)
        assert await stats.flush() == 2
        args = db.calls_of(q.UPSERT_MEMBERS)[0]
        assert args[2] == [5, 6]
        assert args[3] == [1000, 1]
        assert args[4][0] == base + timedelta(seconds=999)
        assert stats.pending == 0

    asyncio.run(scenario())


def test_stats_flush_empty_makes_no_query():
    async def scenario():
        db = FakeDb()
        assert await StatsBuffer(db).flush() == 0
        assert db.calls == []

    asyncio.run(scenario())


def test_stats_failed_flush_keeps_and_merges_counts():
    async def scenario():
        db = FakeDb()
        stats = StatsBuffer(db)
        stats.add_message(1, -100, 5)
        stats.add_message(1, -100, 5)
        db.fail_next = 1
        with pytest.raises(ConnectionError):
            await stats.flush()
        stats.add_message(1, -100, 5)  # пока база лежала, пришло ещё сообщение
        await stats.flush()
        assert db.calls_of(q.UPSERT_MEMBERS)[0][3] == [3]

    asyncio.run(scenario())


def test_stats_user_written_only_when_changed():
    async def scenario():
        db = FakeDb()
        stats = StatsBuffer(db)
        for _ in range(100):
            stats.touch_user(5, "vasya", "Вася")
        await stats.flush()
        assert len(db.calls_of(q.UPSERT_USERS)) == 1
        stats.touch_user(5, "vasya", "Вася")
        assert stats.pending == 0
        stats.touch_user(5, "petya", "Вася")
        assert stats.pending == 1

    asyncio.run(scenario())


def test_stats_known_users_are_bounded():
    stats = StatsBuffer(FakeDb(), known_users=10)
    for user_id in range(100):
        stats.touch_user(user_id, None, "x")
    assert len(stats._known) == 10


def test_stats_threshold_wakes_writer():
    stats = StatsBuffer(FakeDb(), flush_threshold=3)
    stats.add_message(1, 1, 1)
    stats.add_message(1, 1, 2)
    assert not stats._wakeup.is_set()
    stats.add_message(1, 1, 3)
    assert stats._wakeup.is_set()


def test_stats_pending_limit_drops_when_database_is_down():
    async def scenario():
        db = FakeDb()
        stats = StatsBuffer(db, max_pending=2)
        for user_id in range(4):
            stats.add_message(1, 1, user_id)
        db.fail_next = 1
        with pytest.raises(ConnectionError):
            await stats.flush()
        assert stats.dropped_rows == 2

    asyncio.run(scenario())


# --- Состояния диалогов ---

KEY = (1, 2, 3, 0, "default")


def test_fsm_reads_never_touch_database():
    async def scenario():
        db = FakeDb()
        store = FsmStore(db)
        await store.set_state(KEY, "wait")
        await store.set_data(KEY, {"a": 1})
        db.calls.clear()
        for _ in range(100):
            assert store.get_state(KEY) == "wait"
            assert store.get_data(KEY) == {"a": 1}
            assert store.get_state((9, 9, 9, 0, "default")) is None
        assert db.calls == []

    asyncio.run(scenario())


def test_fsm_clearing_deletes_row_and_empty_clear_makes_no_query():
    async def scenario():
        db = FakeDb()
        store = FsmStore(db)
        await store.set_state(KEY, None)
        await store.set_data(KEY, {})
        assert db.calls == []
        await store.set_state(KEY, "wait")
        await store.set_state(KEY, None)
        assert len(db.calls_of(q.FSM_DELETE)) == 1
        assert len(store) == 0

    asyncio.run(scenario())


def test_fsm_failed_write_leaves_memory_unchanged():
    async def scenario():
        db = FakeDb()
        store = FsmStore(db)
        db.fail_next = 1
        with pytest.raises(ConnectionError):
            await store.set_state(KEY, "wait")
        assert store.get_state(KEY) is None

    asyncio.run(scenario())


def test_fsm_get_data_returns_a_copy():
    async def scenario():
        store = FsmStore(FakeDb())
        await store.set_data(KEY, {"a": 1})
        store.get_data(KEY)["a"] = 2
        assert store.get_data(KEY) == {"a": 1}

    asyncio.run(scenario())


def test_fsm_load_restores_rows():
    async def scenario():
        db = FakeDb()
        db.rows = [
            {"bot_id": 1, "chat_id": 2, "user_id": 3, "thread_id": 0, "destiny": "default",
             "state": "wait", "data": {"a": 1}}
        ]
        store = FsmStore(db)
        assert await store.load() == 1
        assert store.get_state(KEY) == "wait"

    asyncio.run(scenario())


# --- Чаты ---


def _chat_row(title="Чат", active=True):
    return {"type": "supergroup", "title": title, "username": None, "is_active": active,
            "settings": {}}


def test_chat_touch_writes_only_on_change():
    async def scenario():
        db = FakeDb()
        db.rows = [_chat_row()]
        repo = ChatRepo(db)
        for _ in range(100):
            await repo.touch(1, -100, "supergroup", "Чат", None)
        assert db.calls_of(q.UPSERT_CHAT) == []
        assert len(db.calls_of(q.GET_CHAT)) == 1

        db.rows = [_chat_row("Новое название")]
        await repo.touch(1, -100, "supergroup", "Новое название", None)
        assert len(db.calls_of(q.UPSERT_CHAT)) == 1

    asyncio.run(scenario())


def test_chat_settings_update_invalidates_cache():
    async def scenario():
        db = FakeDb()
        db.rows = [_chat_row()]
        repo = ChatRepo(db)
        await repo.get(1, -100)
        await repo.update_settings(1, -100, {"flood": 5})
        await repo.get(1, -100)
        assert len(db.calls_of(q.GET_CHAT)) == 2

    asyncio.run(scenario())


# --- Админы ---


def test_rights_from_member():
    owner = SimpleNamespace(status="creator")
    assert rights_from_member(owner).is_owner and rights_from_member(owner).can_restrict
    admin = SimpleNamespace(status="administrator", can_restrict_members=True)
    rights = rights_from_member(admin)
    assert rights.can_restrict and not rights.can_delete and not rights.is_owner


def test_admin_cache_asks_telegram_once_and_resets():
    async def scenario():
        calls = 0

        async def fetch(bot, chat_id):
            nonlocal calls
            calls += 1
            return [SimpleNamespace(status="creator", user=SimpleNamespace(id=7))]

        cache = AdminCache(fetch)
        bot = SimpleNamespace(id=1)
        assert (await cache.get(bot, -100, 7)).is_owner
        assert await cache.get(bot, -100, 8) is None
        assert calls == 1
        cache.invalidate(1, -100)
        await cache.get(bot, -100, 7)
        assert calls == 2

    asyncio.run(scenario())


# --- Цикл опроса ---


def _update(update_id: int) -> SimpleNamespace:
    return SimpleNamespace(update_id=update_id)


def _loop(fetch, handle, limiter=None, on_error=None, **kwargs) -> PollLoop:
    return PollLoop(
        "тест",
        fetch=fetch,
        handle=handle,
        limiter=limiter or asyncio.Semaphore(100),
        on_error=on_error or (lambda exc: ErrorAction(delay=0)),
        backoff_min=0,
        backoff_max=0,
        **kwargs,
    )


def test_poll_processes_updates_and_advances_offset():
    async def scenario():
        batches = [[_update(1), _update(2)], [_update(3)]]
        offsets, seen = [], []

        async def fetch(offset, timeout):
            offsets.append((offset, timeout))
            if batches:
                return batches.pop(0)
            await asyncio.sleep(3600)
            return []

        async def handle(update):
            seen.append(update.update_id)

        loop = _loop(fetch, handle)
        loop.start()
        await asyncio.sleep(0.01)
        await loop.stop()
        assert sorted(seen) == [1, 2, 3]
        assert [o for o, _ in offsets[:3]] == [None, 3, 4]
        # При остановке обработанное подтверждается запросом без ожидания.
        assert offsets[-1] == (4, 0)

    asyncio.run(scenario())


def test_poll_limits_concurrency():
    async def scenario():
        active = peak = 0
        batches = [[_update(i) for i in range(50)]]

        async def fetch(offset, timeout):
            if batches:
                return batches.pop(0)
            await asyncio.sleep(3600)
            return []

        async def handle(update):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.001)
            active -= 1

        loop = _loop(fetch, handle, limiter=asyncio.Semaphore(5))
        loop.start()
        await asyncio.sleep(0.1)
        await loop.stop()
        assert peak == 5
        assert loop.processed == 50

    asyncio.run(scenario())


def test_poll_handler_error_does_not_stop_loop_and_releases_slot():
    async def scenario():
        batches = [[_update(1)], [_update(2)]]
        seen = []
        limiter = asyncio.Semaphore(1)

        async def fetch(offset, timeout):
            if batches:
                return batches.pop(0)
            await asyncio.sleep(3600)
            return []

        async def handle(update):
            seen.append(update.update_id)
            if update.update_id == 1:
                raise ValueError("сбой обработчика")

        loop = _loop(fetch, handle, limiter=limiter)
        loop.start()
        await asyncio.sleep(0.01)
        await loop.stop()
        assert seen == [1, 2]
        assert not limiter.locked()

    asyncio.run(scenario())


def test_poll_retries_after_network_error():
    async def scenario():
        attempts = 0
        seen = []

        async def fetch(offset, timeout):
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise ConnectionError("нет сети")
            if attempts == 3:
                return [_update(1)]
            await asyncio.sleep(3600)
            return []

        async def handle(update):
            seen.append(update.update_id)

        loop = _loop(fetch, handle)
        loop.start()
        await asyncio.sleep(0.01)
        await loop.stop()
        assert seen == [1]

    asyncio.run(scenario())


def test_poll_fatal_error_stops_loop_and_reports():
    async def scenario():
        reasons = []

        async def fetch(offset, timeout):
            raise PermissionError("токен")

        async def handle(update):
            raise AssertionError

        async def on_fatal(reason):
            reasons.append(reason)

        loop = _loop(
            fetch, handle,
            on_error=lambda exc: ErrorAction(fatal=True, reason="токен отозван"),
            on_fatal=on_fatal,
        )
        loop.start()
        await asyncio.sleep(0.01)
        assert not loop.running
        assert reasons == ["токен отозван"]
        await loop.stop()

    asyncio.run(scenario())


def test_poll_stop_while_waiting_for_slot_does_not_lose_update():
    async def scenario():
        batches = [[_update(1), _update(2)]]
        confirmed = []
        release = asyncio.Event()

        async def fetch(offset, timeout):
            if timeout == 0:
                confirmed.append(offset)
                return []
            if batches:
                return batches.pop(0)
            await asyncio.sleep(3600)
            return []

        async def handle(update):
            await release.wait()

        loop = _loop(fetch, handle, limiter=asyncio.Semaphore(1))
        loop.start()
        await asyncio.sleep(0.01)
        release.set()
        stop = asyncio.create_task(loop.stop())
        await stop
        # Второе событие не начинали: подтверждён только первый номер.
        assert confirmed == [2]

    asyncio.run(scenario())


# --- Настройки и прочее ---

BASE_ENV = {"BOT_TOKEN": "1:a", "OWNER_IDS": "1, 2", "DATABASE_URL": "postgresql://x"}


def test_settings_parse():
    settings = Settings.from_env(BASE_ENV)
    assert settings.owner_ids == {1, 2}
    assert settings.secret_key is None


@pytest.mark.parametrize(
    "change",
    [{"BOT_TOKEN": ""}, {"OWNER_IDS": "abc"}, {"OWNER_IDS": " , "}, {"DB_POOL_SIZE": "много"}],
)
def test_settings_errors(change):
    with pytest.raises(ConfigError):
        Settings.from_env({**BASE_ENV, **change})


def test_load_dotenv_does_not_override(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# комментарий\nA=1\nB='2'\n\nC=x=y\n", encoding="utf-8")
    env = {"A": "оставить"}
    load_dotenv(path, env)
    assert env == {"A": "оставить", "B": "2", "C": "x=y"}


def test_token_cipher_roundtrip_and_wrong_key():
    from cryptography.fernet import InvalidToken

    blob = TokenCipher("ключ").encrypt("123:abc")
    assert b"123:abc" not in blob
    assert TokenCipher("ключ").decrypt(blob) == "123:abc"
    with pytest.raises(InvalidToken):
        TokenCipher("другой").decrypt(blob)


@pytest.mark.parametrize(
    "content,is_bot,sender,expected",
    [
        ("text", False, False, True),
        ("sticker", False, False, True),
        ("new_chat_members", False, False, False),
        ("pinned_message", False, False, False),
        ("text", True, False, False),
        ("text", False, True, False),
    ],
)
def test_is_countable(content, is_bot, sender, expected):
    assert is_countable(content, is_bot=is_bot, has_sender_chat=sender) is expected


def test_rss_mb_handles_missing_file():
    assert rss_mb("/нет/такого/файла") is None


def test_texts_escape_values_but_keep_template_html():
    text = t("me_private", name="<b>взлом</b>", total=1, chats=1)
    assert "&lt;b&gt;взлом&lt;/b&gt;" in text
    assert "<b>&lt;b&gt;" in text


def test_migrate_applies_only_new_files_in_order(tmp_path):
    async def scenario():
        (tmp_path / "002_b.sql").write_text("SELECT 2", encoding="utf-8")
        (tmp_path / "001_a.sql").write_text("SELECT 1", encoding="utf-8")
        db = FakeDb()
        db.rows = [{"version": "001_a.sql"}]
        assert await migrate(db, tmp_path) == ["002_b.sql"]
        assert ("SELECT 2", ()) in db.calls
        assert ("SELECT 1", ()) not in db.calls

    asyncio.run(scenario())


def test_migration_files_exist():
    assert [p.name for p in migration_files()][0] == "001_core.sql"
