from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from bot.admins import OWNER_RIGHTS, AdminCache, AdminRights
from bot.db import queries as q
from bot.moderation.service import Moderation, ModerationError, Person
from bot.protection.detector import (
    MessageView,
    find_violation,
    is_allowed,
    is_telegram_link,
    normalize,
)
from bot.protection.service import Incoming, Protection, WhitelistRepo
from bot.protection.settings import ProtectionSettings as Settings
from bot.protection.settings import toggle
from bot.protection.tracker import Tracker

from .fakes import FakeDb
from .test_moderation import FakeRepo, FakeTelegram

BOT = SimpleNamespace(id=777)
CHAT = -100
VASYA = Person(5, "Вася")

# --- Настройки ---


def test_settings_defaults_and_bad_values_from_database():
    assert Settings.from_chat(None) == Settings()
    assert Settings.from_chat({"protect": "мусор"}) == Settings()
    settings = Settings.from_chat({"protect": {
        "flood_limit": 100000, "flood_window": -5, "link_mode": "взлом", "flood_action": "rm -rf",
        "stopwords": ["казино", "", 5], "flood_enabled": "да", "mention_limit": True,
    }})
    assert settings.flood_limit == 50 and settings.flood_window == 1
    assert settings.link_mode == "telegram" and settings.flood_action == "mute"
    assert settings.stopwords == ("казино", "5")
    assert settings.flood_enabled is True and settings.mention_limit == 6


def test_settings_roundtrip_json():
    original = Settings(stopwords=("а",), link_allow=("x.com",), flood_limit=9)
    assert Settings.from_chat({"protect": original.to_json()}) == original


def test_toggle_cycles_and_rejects_unknown():
    settings = Settings()
    assert toggle(settings, "flood").flood_enabled is False
    assert toggle(settings, "links").link_mode == "all"
    assert toggle(toggle(settings, "links"), "links").link_mode == "off"
    assert toggle(toggle(toggle(settings, "links"), "links"), "links").link_mode == "telegram"
    assert toggle(settings, "mentions").mention_limit == 0
    assert toggle(toggle(settings, "mentions"), "mentions").mention_limit == 6
    assert toggle(settings, "action").spam_action == "delete"
    with pytest.raises(ValueError):
        toggle(settings, "__class__")


# --- Ссылки ---


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://t.me/+abcdef", True), ("t.me/joinchat/abc", True), ("T.ME/channel", True),
        ("http://telegram.me/x", True), ("tg://join?invite=abc", True), ("https://name.t.me", True),
        ("https://example.com", False), ("https://not-t.me/x", False),
        ("https://t.me.evil.com/x", False), ("http://[плохой", False),
    ],
)
def test_is_telegram_link(url, expected):
    assert is_telegram_link(url) is expected


@pytest.mark.parametrize(
    "url,allow,own,expected",
    [
        ("https://t.me/mychat", (), "MyChat", True),
        ("https://t.me/mychat/15", (), "mychat", True),
        ("https://t.me/other", (), "mychat", False),
        ("https://example.com/a", ("example.com",), None, True),
        ("https://sub.example.com", ("example.com",), None, True),
        ("https://evilexample.com", ("example.com",), None, False),
        ("https://t.me/partner/5", ("t.me/partner",), None, True),
        ("https://t.me/partner2", ("t.me/partner",), None, False),
    ],
)
def test_is_allowed(url, allow, own, expected):
    assert is_allowed(url, allow, own) is expected


def test_links_modes():
    telegram = MessageView(links=("https://t.me/+x",))
    other = MessageView(links=("https://example.com",))
    assert find_violation(telegram, Settings()) == "links"
    assert find_violation(other, Settings()) is None
    assert find_violation(other, Settings(link_mode="all")) == "links"
    assert find_violation(telegram, Settings(link_mode="off")) is None
    assert find_violation(telegram, Settings(link_allow=("t.me",))) is None


def test_own_chat_link_is_allowed():
    view = MessageView(links=("https://t.me/mychat/5",))
    assert find_violation(view, Settings(), "mychat") is None


def test_other_rules():
    assert find_violation(MessageView(mentions=6), Settings()) == "mentions"
    assert find_violation(MessageView(mentions=5), Settings()) is None
    assert find_violation(MessageView(mentions=50), Settings(mention_limit=0)) is None
    forward = MessageView(is_channel_forward=True)
    assert find_violation(forward, Settings()) is None
    assert find_violation(forward, Settings(block_forwards=True)) == "forward"


def test_stopwords_catch_lookalike_letters_and_word_start_only():
    settings = Settings(stopwords=("казино",))
    assert find_violation(MessageView(text="Лучшее КАЗИНО тут"), settings) == "stopword"
    assert find_violation(MessageView(text="лучшее кaзинo тут"), settings) == "stopword"  # латиница
    assert find_violation(MessageView(text="в казиношке"), settings) == "stopword"
    assert find_violation(MessageView(text="суперказино"), settings) is None
    assert find_violation(MessageView(text="обычный текст"), settings) is None


def test_stopwords_cannot_inject_regex():
    settings = Settings(stopwords=("(a+)+$", ".*"))
    assert find_violation(MessageView(text="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa!"), settings) is None
    assert find_violation(MessageView(text="вот .* тут"), settings) == "stopword"


def test_normalize():
    assert normalize("КАЗИНО") == normalize("кaзинo")


# --- Счётчики ---


def _tracker():
    now = [0.0]
    return Tracker(clock=lambda: now[0]), now


FLOOD = {"flood_limit": 3, "flood_window": 5}


def test_flood_triggers_at_limit_and_returns_all_ids():
    tracker, now = _tracker()
    assert not tracker.hit((1, 1, 1), 10, **FLOOD).flood
    assert not tracker.hit((1, 1, 1), 11, **FLOOD).flood
    hit = tracker.hit((1, 1, 1), 12, **FLOOD)
    assert hit.flood and hit.message_ids == (10, 11, 12)


def test_flood_ignores_slow_messages():
    tracker, now = _tracker()
    for i in range(20):
        now[0] += 3
        assert not tracker.hit((1, 1, 1), i, **FLOOD).flood


def test_flood_cooldown_then_recovers():
    tracker, now = _tracker()
    for i in range(3):
        hit = tracker.hit((1, 1, 1), i, **FLOOD)
    assert hit.flood
    during = tracker.hit((1, 1, 1), 9, **FLOOD)
    assert during.cooldown and during.message_ids == (9,) and not during.flood
    now[0] += 6
    assert not tracker.hit((1, 1, 1), 10, **FLOOD).cooldown


def test_album_counts_once():
    tracker, _ = _tracker()
    for i in range(10):
        assert not tracker.hit((1, 1, 1), i, album="альбом", **FLOOD).flood


def test_people_and_chats_are_counted_separately():
    tracker, _ = _tracker()
    for i in range(2):
        tracker.hit((1, 1, 1), i, **FLOOD)
    assert not tracker.hit((1, 1, 2), 5, **FLOOD).flood
    assert not tracker.hit((1, 2, 1), 6, **FLOOD).flood
    assert not tracker.hit((2, 1, 1), 7, **FLOOD).flood
    assert tracker.hit((1, 1, 1), 8, **FLOOD).flood


DUP = {"duplicate_limit": 3, "duplicate_window": 60}


def test_duplicates_trigger_and_short_texts_are_ignored():
    tracker, now = _tracker()
    text = "купи рекламу у нас"
    assert not tracker.hit((1, 1, 1), 1, text=text, **DUP).duplicate
    assert not tracker.hit((1, 1, 1), 2, text=text, **DUP).duplicate
    assert tracker.hit((1, 1, 1), 3, text=text, **DUP).duplicate
    for i in range(10):
        assert not tracker.hit((1, 1, 2), i, text="да", **DUP).duplicate


def test_duplicates_reset_on_other_text_and_timeout():
    tracker, now = _tracker()
    text = "купи рекламу у нас"
    tracker.hit((1, 1, 1), 1, text=text, **DUP)
    tracker.hit((1, 1, 1), 2, text=text, **DUP)
    tracker.hit((1, 1, 1), 3, text="другой длинный текст", **DUP)
    assert not tracker.hit((1, 1, 1), 4, text=text, **DUP).duplicate
    now[0] += 61
    assert not tracker.hit((1, 1, 1), 5, text=text, **DUP).duplicate


def test_tracker_memory_is_bounded():
    tracker = Tracker(maxsize=100)
    for user in range(1000):
        tracker.hit((1, 1, user), 1, **FLOOD)
    assert len(tracker) == 100


def test_changed_limit_applies_to_existing_entry():
    tracker, _ = _tracker()
    tracker.hit((1, 1, 1), 1, **FLOOD)
    assert tracker.hit((1, 1, 1), 2, flood_limit=2, flood_window=5).flood


# --- Решения ---


class FakeMessenger:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.sent: list[tuple[str, int | None]] = []

    async def delete_many(self, chat_id, message_ids):
        self.deleted.extend(message_ids)

    async def send(self, chat_id, thread_id, text, undo_id=None):
        self.sent.append((text, undo_id))
        return 1000 + len(self.sent)

    async def delete(self, chat_id, message_id):
        self.deleted.append(message_id)


WEAK_BOT = AdminRights(False, False, False, False, False, False, False)


def _setup(*, bot_rights: AdminRights = OWNER_RIGHTS, admins=(1,), fail_admins: bool = False):
    async def fetch(bot, chat_id):
        if fail_admins:
            raise ConnectionError("Telegram недоступен")
        rights = {user_id: OWNER_RIGHTS for user_id in admins}
        rights[BOT.id] = bot_rights
        return [
            SimpleNamespace(
                status="creator" if r.is_owner else "administrator",
                user=SimpleNamespace(id=user_id),
                can_restrict_members=r.can_restrict,
                can_delete_messages=r.can_delete,
            )
            for user_id, r in rights.items()
        ]

    cache = AdminCache(fetch)
    repo = FakeRepo()
    db = FakeDb()
    protection = Protection(Moderation(repo, cache), WhitelistRepo(db))
    return protection, repo, db, FakeTelegram(), FakeMessenger()


def _item(view: MessageView | None = None, *, author: Person = VASYA, message_id: int = 50):
    return Incoming(CHAT, None, author, message_id, None, None, view or MessageView())


SPAM = MessageView(links=("https://t.me/+spam",))


async def _inspect(protection, tg, messenger, item, settings: dict[str, Any] | None = None):
    return await protection.inspect(BOT, tg, messenger, settings or {}, item)


def test_ordinary_message_costs_no_queries_and_no_telegram_calls():
    async def scenario():
        protection, _, db, tg, messenger = _setup()
        calls = 0
        original = protection._moderation.admins._fetch

        async def counting(bot, chat_id):
            nonlocal calls
            calls += 1
            return await original(bot, chat_id)

        protection._moderation.admins._fetch = counting
        raw: dict[str, Any] = {}
        for i in range(5):
            assert not await protection.inspect(
                BOT, tg, messenger, raw, _item(MessageView(text=f"обычный текст {i}"), message_id=i)
            )
            await asyncio.sleep(1 / 1000)
        assert db.calls == [] and calls == 0 and tg.calls == [] and messenger.deleted == []

    asyncio.run(scenario())


def test_spam_link_is_deleted_and_warned():
    async def scenario():
        protection, repo, _, tg, messenger = _setup()
        assert await _inspect(protection, tg, messenger, _item(SPAM))
        assert messenger.deleted == [50]
        assert repo.rows[0]["kind"] == "warn" and repo.rows[0]["admin_id"] == 0
        text, undo = messenger.sent[0]
        assert "1/3" in text and "Автоматически" in text and "ссылка" in text and undo == 1

    asyncio.run(scenario())


def test_delete_only_mode_sends_notice_that_disappears():
    async def scenario():
        protection, repo, _, tg, messenger = _setup()
        raw = {"protect": {"spam_action": "delete", "notice_seconds": 3}}
        assert await _inspect(protection, tg, messenger, _item(SPAM), raw)
        assert repo.rows == []
        assert "удалено" in messenger.sent[0][0]
        task = next(iter(protection._tasks))
        task.cancel()

    asyncio.run(scenario())


@pytest.mark.parametrize("who", ["admin", "whitelist"])
def test_admins_and_whitelist_are_exempt(who):
    async def scenario():
        protection, repo, db, tg, messenger = _setup()
        author = Person(1, "Админ") if who == "admin" else VASYA
        db.rows = [{"user_id": 5}]
        assert not await _inspect(protection, tg, messenger, _item(SPAM, author=author))
        assert messenger.deleted == [] and repo.rows == []

    asyncio.run(scenario())


def test_flood_deletes_everything_and_mutes_once():
    async def scenario():
        protection, repo, _, tg, messenger = _setup()
        raw = {"protect": {"flood_limit": 3, "flood_window": 5, "flood_seconds": 600}}
        results = [
            await _inspect(protection, tg, messenger, _item(message_id=i), raw) for i in (1, 2, 3)
        ]
        assert results == [False, False, True]
        assert messenger.deleted == [1, 2, 3]
        assert [call[0] for call in tg.calls] == ["mute"]
        assert repo.rows[0]["source"] == "flood"
        assert "флуд" in messenger.sent[0][0]

        assert await _inspect(protection, tg, messenger, _item(message_id=4), raw)
        assert messenger.deleted[-1] == 4 and len(tg.calls) == 1  # второй раз не наказывает

    asyncio.run(scenario())


def test_flood_disabled_lets_messages_through():
    async def scenario():
        protection, _, _, tg, messenger = _setup()
        raw = {"protect": {"flood_enabled": False, "flood_limit": 2}}
        for i in range(10):
            assert not await _inspect(protection, tg, messenger, _item(message_id=i), raw)

    asyncio.run(scenario())


def test_no_delete_right_tells_once_and_does_nothing():
    async def scenario():
        protection, repo, _, tg, messenger = _setup(bot_rights=WEAK_BOT)
        for i in range(5):
            assert not await _inspect(protection, tg, messenger, _item(SPAM, message_id=i))
        assert messenger.deleted == [] and repo.rows == []
        assert len(messenger.sent) == 1 and "нет права" in messenger.sent[0][0]

    asyncio.run(scenario())


def test_telegram_failure_never_punishes_at_random():
    async def scenario():
        protection, repo, _, tg, messenger = _setup(fail_admins=True)
        assert not await _inspect(protection, tg, messenger, _item(SPAM))
        assert messenger.deleted == [] and repo.rows == []

    asyncio.run(scenario())


def test_refused_punishment_keeps_deletion_and_does_not_crash():
    async def scenario():
        protection, _, _, tg, messenger = _setup()
        tg.fail = ModerationError("target_admin")
        raw = {"protect": {"flood_limit": 2, "flood_window": 5}}
        await _inspect(protection, tg, messenger, _item(message_id=1), raw)
        assert await _inspect(protection, tg, messenger, _item(message_id=2), raw)
        assert messenger.deleted == [1, 2]

    asyncio.run(scenario())


def test_raid_does_not_flood_chat_with_cards():
    async def scenario():
        protection, repo, _, tg, messenger = _setup()
        for user in range(100, 120):
            await _inspect(
                protection, tg, messenger, _item(SPAM, author=Person(user, "Бот"), message_id=user)
            )
        assert len(repo.rows) == 20  # наказаны все
        assert len(messenger.deleted) == 20
        assert len(messenger.sent) == 1  # а карточка одна

    asyncio.run(scenario())


def test_settings_are_parsed_once_for_same_object():
    protection, _, _, _, _ = _setup()
    raw = {"protect": {"flood_limit": 9}}
    first = protection.settings_for(1, 1, raw)
    assert protection.settings_for(1, 1, raw) is first
    changed = {"protect": {"flood_limit": 8}}
    assert protection.settings_for(1, 1, changed).flood_limit == 8


def test_whitelist_is_cached_until_changed():
    async def scenario():
        db = FakeDb()
        db.rows = [{"user_id": 5}]
        repo = WhitelistRepo(db)
        for _ in range(10):
            assert 5 in await repo.ids(1, 1)
        assert len(db.calls_of(q.WHITELIST_IDS)) == 1
        await repo.add(1, 1, 6, 9)
        await repo.ids(1, 1)
        assert len(db.calls_of(q.WHITELIST_IDS)) == 2

    asyncio.run(scenario())
