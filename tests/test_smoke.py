"""Сквозные тесты: настоящие aiogram, asyncpg и Postgres, подставной только Telegram.

Запускаются там, где установлены зависимости и задан TEST_PG_DSN (в CI и на сервере).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest

aiogram = pytest.importorskip("aiogram")
asyncpg = pytest.importorskip("asyncpg")

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramUnauthorizedError  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.methods import (  # noqa: E402
    BanChatMember,
    DeleteMessage,
    EditMessageText,
    GetChatAdministrators,
    GetMe,
    GetUpdates,
    RestrictChatMember,
    SendMessage,
    SetMyCommands,
    UnbanChatMember,
)
from aiogram.types import (  # noqa: E402
    Chat,
    ChatMemberAdministrator,
    ChatMemberOwner,
    Message,
    Update,
    User,
)

from bot.app import build_app, build_dispatcher  # noqa: E402
from bot.config import Settings  # noqa: E402
from bot.db import queries as q  # noqa: E402
from bot.db.migrate import migrate  # noqa: E402
from bot.db.pool import Database  # noqa: E402
from bot.runtime import ALLOWED_UPDATES, BotManager, CloneError  # noqa: E402
from bot.storage import PgStorage  # noqa: E402

DSN = os.environ.get("TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="нужен TEST_PG_DSN")

BOT_ID = 123456
TOKEN = f"{BOT_ID}:{'A' * 35}"
CHAT_ID = -1001


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Any] = []
        self.updates: dict[int, list[dict[str, Any]]] = {}
        self.revoked: set[int] = set()

    async def close(self) -> None:
        return None

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:
        self.sent.append(method)
        if bot.id in self.revoked:
            raise TelegramUnauthorizedError(method=method, message="Unauthorized")
        if isinstance(method, GetUpdates):
            queue = self.updates.get(bot.id)
            if queue:
                return [Update.model_validate(queue.pop(0), context={"bot": bot})]
            if method.timeout:
                await asyncio.sleep(0.01)
            return []
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Mod", username=f"bot{bot.id}")
        if isinstance(method, GetChatAdministrators):
            owner = User(id=1, is_bot=False, first_name="Owner")
            me = User(id=BOT_ID, is_bot=True, first_name="Mod")
            flags = dict.fromkeys(
                (
                    "can_be_edited", "is_anonymous", "can_manage_chat", "can_delete_messages",
                    "can_manage_video_chats", "can_restrict_members", "can_promote_members",
                    "can_change_info", "can_invite_users", "can_post_stories",
                    "can_edit_stories", "can_delete_stories", "can_send_welcome_messages",
                ),
                True,
            )
            return [
                ChatMemberOwner(user=owner, is_anonymous=False),
                ChatMemberAdministrator(user=me, **flags),
            ]
        if isinstance(method, SendMessage):
            chat = Chat(id=method.chat_id, type="supergroup")
            return Message(message_id=999, date=0, chat=chat, text=method.text)
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""

    def texts(self) -> list[str]:
        return [m.text for m in self.sent if isinstance(m, SendMessage)]


class Env:
    def __init__(self, app: Any, dp: Any, bot: Bot, session: FakeSession) -> None:
        self.app, self.dp, self.bot, self.session = app, dp, bot, session
        self._update_id = 0

    async def feed(self, **fields: Any) -> None:
        self._update_id += 1
        update = Update.model_validate(
            {"update_id": self._update_id, **fields}, context={"bot": self.bot}
        )
        await self.dp.feed_update(self.bot, update)

    async def say(self, text: str, *, user_id: int = 5, chat_id: int = CHAT_ID, **extra: Any):
        await self.feed(message={
            "message_id": self._update_id + 1,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "supergroup", "title": "Чат"},
            "from": {
                "id": user_id, "is_bot": False, "first_name": "Вася",
                # У каждого свой username: один и тот же у двоих запутал бы поиск по нему.
                "username": "vasya" if user_id == 5 else f"user{user_id}",
            },
            "text": text,
            **extra,
        })


def scenario(body):
    """Поднимает окружение в отдельной схеме базы и выполняет сценарий."""

    async def run() -> None:
        schema = f"s_{uuid.uuid4().hex[:10]}"
        admin = await asyncpg.connect(DSN)
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        sep = "&" if "?" in DSN else "?"
        db = await Database.connect(f"{DSN}{sep}search_path={schema}")
        try:
            await migrate(db)
            await db.execute(q.UPSERT_MAIN_BOT, BOT_ID, "mod_bot", "Mod", 1)
            settings = Settings(bot_token=TOKEN, owner_ids=frozenset({1}), database_url=DSN)
            app = build_app(settings, db)
            dp = build_dispatcher(app)
            session = FakeSession()
            bot = Bot(TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
            await body(Env(app, dp, bot, session))
        finally:
            await db.close()
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await admin.close()

    asyncio.run(run())


def test_group_message_registers_chat_and_counts():
    async def body(env: Env) -> None:
        await env.say("привет")
        await env.say("ещё раз")
        await env.app.stats.flush()
        count = await env.app.db.fetchval(q.MEMBER_COUNT_IN_CHAT, BOT_ID, CHAT_ID, 5)
        assert count == 2
        info = await env.app.chats.get(BOT_ID, CHAT_ID)
        assert info.title == "Чат"
        assert (await env.app.db.fetchrow(q.GET_USER, 5))["username"] == "vasya"

    scenario(body)


def test_me_shows_counts_across_chats():
    async def body(env: Env) -> None:
        await env.say("раз")
        await env.say("два", chat_id=-1002)
        await env.say("/me")
        reply = env.session.texts()[-1]
        assert "<b>2</b>" in reply  # в этом чате, вместе с самой командой
        assert "<b>3</b>" in reply  # всего во всех чатах

    scenario(body)


def test_ping_answers_only_admins_in_group():
    async def body(env: Env) -> None:
        await env.say("/ping", user_id=5)
        assert env.session.texts() == []
        await env.say("/ping", user_id=1)
        assert "на связи" in env.session.texts()[-1]

    scenario(body)


def test_service_message_is_not_counted():
    async def body(env: Env) -> None:
        await env.feed(message={
            "message_id": 1,
            "date": 1_700_000_000,
            "chat": {"id": CHAT_ID, "type": "supergroup", "title": "Чат"},
            "from": {"id": 5, "is_bot": False, "first_name": "Вася"},
            "new_chat_members": [{"id": 5, "is_bot": False, "first_name": "Вася"}],
        })
        await env.app.stats.flush()
        assert await env.app.db.fetchval(q.MEMBER_COUNT_IN_CHAT, BOT_ID, CHAT_ID, 5) is None

    scenario(body)


def _membership(old: str, new: str) -> dict[str, Any]:
    bot_user = {"id": BOT_ID, "is_bot": True, "first_name": "Mod"}

    def member(status: str) -> dict[str, Any]:
        data: dict[str, Any] = {"status": status, "user": bot_user}
        if status == "kicked":
            data["until_date"] = 0
        if status == "administrator":
            data.update(
                can_be_edited=False, is_anonymous=False, can_manage_chat=True,
                can_delete_messages=True, can_manage_video_chats=False,
                can_restrict_members=True, can_promote_members=False, can_change_info=False,
                can_invite_users=True, can_post_stories=False, can_edit_stories=False,
                can_delete_stories=False, can_send_welcome_messages=False,
            )
        return data

    return {
        "chat": {"id": CHAT_ID, "type": "supergroup", "title": "Чат"},
        "from": {"id": 1, "is_bot": False, "first_name": "Owner"},
        "date": 1_700_000_000,
        "old_chat_member": member(old),
        "new_chat_member": member(new),
    }


def test_bot_added_promoted_and_removed():
    async def body(env: Env) -> None:
        await env.feed(my_chat_member=_membership("left", "member"))
        assert "Спасибо" in env.session.texts()[-1]
        assert (await env.app.chats.get(BOT_ID, CHAT_ID)).is_active

        await env.feed(my_chat_member=_membership("member", "administrator"))
        assert "Права получены" in env.session.texts()[-1]

        await env.feed(my_chat_member=_membership("administrator", "kicked"))
        assert not (await env.app.chats.get(BOT_ID, CHAT_ID)).is_active
        assert await env.app.chats.count_active(BOT_ID) == 0

    scenario(body)


def test_group_migration_moves_counts_including_unflushed():
    async def body(env: Env) -> None:
        await env.say("раз")  # ещё не записано в базу
        await env.feed(message={
            "message_id": 50,
            "date": 1_700_000_000,
            "chat": {"id": CHAT_ID, "type": "group", "title": "Чат"},
            "from": {"id": 1, "is_bot": False, "first_name": "Owner"},
            "migrate_to_chat_id": -1009,
        })
        await env.app.stats.flush()
        assert await env.app.db.fetchval(q.MEMBER_COUNT_IN_CHAT, BOT_ID, -1009, 5) == 1
        assert await env.app.chats.get(BOT_ID, CHAT_ID) is None
        assert (await env.app.chats.get(BOT_ID, -1009)).type == "supergroup"

    scenario(body)


def test_settings_survive_jsonb_roundtrip():
    async def body(env: Env) -> None:
        await env.say("раз")
        await env.app.chats.update_settings(BOT_ID, CHAT_ID, {"flood": {"limit": 5}})
        info = await env.app.chats.get(BOT_ID, CHAT_ID)
        assert info.settings == {"flood": {"limit": 5}}

    scenario(body)


def test_fsm_state_survives_restart_without_read_queries():
    async def body(env: Env) -> None:
        storage = PgStorage(env.app.fsm)
        key = StorageKey(bot_id=BOT_ID, chat_id=1, user_id=2)
        await storage.set_state(key, "wait")
        await storage.update_data(key, {"step": 1})

        fresh = type(env.app.fsm)(env.app.db)
        assert await fresh.load() == 1
        restored = PgStorage(fresh)
        assert await restored.get_state(key) == "wait"
        assert await restored.get_data(key) == {"step": 1}

    scenario(body)


# --- Модерация ---

ADMIN_USER = {"id": 1, "is_bot": False, "first_name": "Owner"}


def _sent_of(env: Env, method_type: type) -> list[Any]:
    return [m for m in env.session.sent if isinstance(m, method_type)]


async def _command(env: Env, text: str, *, reply_to: int | None = 5, user_id: int = 1) -> None:
    extra: dict[str, Any] = {}
    if reply_to is not None:
        extra["reply_to_message"] = {
            "message_id": 1,
            "date": 1_700_000_000,
            "chat": {"id": CHAT_ID, "type": "supergroup", "title": "Чат"},
            "from": {"id": reply_to, "is_bot": False, "first_name": "Вася"},
            "text": "плохое сообщение",
        }
    await env.say(text, user_id=user_id, **extra)


async def _press(env: Env, data: str, *, user_id: int = 1) -> None:
    await env.feed(callback_query={
        "id": "1",
        "from": {"id": user_id, "is_bot": False, "first_name": "Owner"},
        "chat_instance": "x",
        "data": data,
        "message": {
            "message_id": 77,
            "date": 1_700_000_000,
            "chat": {"id": CHAT_ID, "type": "supergroup", "title": "Чат"},
            "text": "карточка",
        },
    })


def test_mute_command_restricts_and_sends_small_card_with_button():
    async def body(env: Env) -> None:
        await _command(env, "/mute 2ч спам")
        restrict = _sent_of(env, RestrictChatMember)[-1]
        assert restrict.user_id == 5 and restrict.until_date is not None
        assert restrict.permissions.can_send_messages is False
        card = _sent_of(env, SendMessage)[-1]
        assert "Мут" in card.text and "2 ч" in card.text and "спам" in card.text
        assert card.reply_markup.inline_keyboard[0][0].callback_data.startswith("mod:u:")
        # Команда админа удалена, чтобы чат оставался чистым.
        assert _sent_of(env, DeleteMessage)

    scenario(body)


def test_russian_alias_and_bang_prefix():
    async def body(env: Env) -> None:
        await _command(env, "!бан реклама")
        assert _sent_of(env, BanChatMember)[-1].user_id == 5
        assert "Бан" in env.session.texts()[-1]

    scenario(body)


def test_regular_member_is_ignored_silently():
    async def body(env: Env) -> None:
        await _command(env, "/ban", user_id=6)
        assert not _sent_of(env, BanChatMember)
        assert env.session.texts() == []

    scenario(body)


def test_cannot_punish_admin_or_bot():
    async def body(env: Env) -> None:
        await _command(env, "/ban", reply_to=BOT_ID)
        assert "Меня наказать нельзя" in env.session.texts()[-1]
        await _command(env, "/ban", reply_to=1)
        assert "Себя наказать нельзя" in env.session.texts()[-1]
        assert not _sent_of(env, BanChatMember)

    scenario(body)


def test_target_by_username_from_earlier_message():
    async def body(env: Env) -> None:
        await env.say("привет", user_id=5)  # username vasya ещё не записан в базу
        await _command(env, "/kick @vasya флуд", reply_to=None)
        assert _sent_of(env, BanChatMember)[-1].user_id == 5
        assert _sent_of(env, UnbanChatMember)[-1].user_id == 5
        assert "Кик" in env.session.texts()[-1]

    scenario(body)


def test_unknown_target_and_missing_target_explain_what_to_do():
    async def body(env: Env) -> None:
        await _command(env, "/ban @nobody_here", reply_to=None)
        assert "ещё не видел" in env.session.texts()[-1]
        await _command(env, "/ban", reply_to=None)
        assert "Кого?" in env.session.texts()[-1]
        await _command(env, "/mute 10с")
        assert "30 секунд" in env.session.texts()[-1]

    scenario(body)


def test_undo_button_lifts_mute_and_only_admin_can_press():
    async def body(env: Env) -> None:
        await _command(env, "/mute спам")
        data = _sent_of(env, SendMessage)[-1].reply_markup.inline_keyboard[0][0].callback_data

        await _press(env, data, user_id=6)
        assert len(_sent_of(env, RestrictChatMember)) == 1  # обычный участник не снимет

        await _press(env, data)
        lifted = _sent_of(env, RestrictChatMember)[-1]
        assert lifted.permissions.can_send_messages is True
        assert "Мут снят" in _sent_of(env, EditMessageText)[-1].text

        before = len(_sent_of(env, RestrictChatMember))
        await _press(env, data)  # двойное нажатие
        assert len(_sent_of(env, RestrictChatMember)) == before

    scenario(body)


def test_three_warns_lead_to_mute_and_reset():
    async def body(env: Env) -> None:
        await _command(env, "/warn раз")
        await _command(env, "/warn два")
        assert not _sent_of(env, RestrictChatMember)
        await _command(env, "/warn три")
        assert _sent_of(env, RestrictChatMember)[-1].user_id == 5
        assert "3/3" in env.session.texts()[-1] and "Лимит" in env.session.texts()[-1]
        await _command(env, "/warns")
        assert "0/3" in env.session.texts()[-1]

    scenario(body)


def test_history_and_journal_show_records():
    async def body(env: Env) -> None:
        await _command(env, "/mute 1ч спам")
        await _command(env, "/history")
        assert "спам" in env.session.texts()[-1]
        await _command(env, "/journal", reply_to=None)
        assert "Журнал" in env.session.texts()[-1] and "спам" in env.session.texts()[-1]

    scenario(body)


def test_anonymous_admin_confirms_with_button():
    async def body(env: Env) -> None:
        await env.feed(message={
            "message_id": 40,
            "date": 1_700_000_000,
            "chat": {"id": CHAT_ID, "type": "supergroup", "title": "Чат"},
            "from": {"id": 1087968824, "is_bot": True, "first_name": "Group",
                     "username": "GroupAnonymousBot"},
            "sender_chat": {"id": CHAT_ID, "type": "supergroup", "title": "Чат"},
            "text": "/ban 555666 реклама",
        })
        assert not _sent_of(env, BanChatMember)
        data = _sent_of(env, SendMessage)[-1].reply_markup.inline_keyboard[0][0].callback_data
        assert data.startswith("mod:a:")

        await _press(env, data, user_id=6)  # чужой человек подтвердить не может
        assert not _sent_of(env, BanChatMember)

        await _press(env, data)
        assert _sent_of(env, BanChatMember)[-1].user_id == 555666

        before = len(_sent_of(env, BanChatMember))
        await _press(env, data)  # повторно кнопка уже не сработает
        assert len(_sent_of(env, BanChatMember)) == before

    scenario(body)


def test_private_chat_is_closed_for_everyone_except_owner():
    async def body(env: Env) -> None:
        private = {"id": 6, "type": "private", "first_name": "Вася"}
        stranger = {"id": 6, "is_bot": False, "first_name": "Вася"}
        for text in ("/start", "/me", "/ping", "/status"):
            await env.feed(message={
                "message_id": 1, "date": 1_700_000_000, "chat": private,
                "from": stranger, "text": text,
            })
        assert env.session.texts() == []

        owner_chat = {"id": 1, "type": "private", "first_name": "Owner"}
        await env.feed(message={
            "message_id": 2, "date": 1_700_000_000, "chat": owner_chat,
            "from": ADMIN_USER, "text": "/start",
        })
        assert env.session.texts()

    scenario(body)


# --- Запуск ботов ---

CLONE_ID = 654321
CLONE_TOKEN = f"{CLONE_ID}:{'B' * 35}"


def _raw_update(update_id: int, chat_id: int, user_id: int = 5) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": "supergroup", "title": "Чат"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Вася"},
            "text": "привет",
        },
    }


def _manager(env: Env, **kwargs: Any) -> BotManager:
    options: dict[str, Any] = {
        "owner_id": 1,
        "secret_key": "ключ",
        "polling_timeout": 1,
        "max_concurrent_updates": 10,
        "on_main_fatal": lambda: None,
        "session": env.session,
        **kwargs,
    }
    return BotManager(env.dp, env.app.db, **options)


async def _until(condition: Any) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("условие не выполнилось вовремя")


def test_main_bot_polls_processes_and_confirms_on_stop():
    async def body(env: Env) -> None:
        manager = _manager(env)
        await manager.start(TOKEN)
        assert any(isinstance(m, SetMyCommands) for m in env.session.sent)

        env.session.updates[BOT_ID] = [_raw_update(10, CHAT_ID), _raw_update(11, CHAT_ID)]
        await _until(lambda: env.app.stats.pending >= 2 and not env.session.updates[BOT_ID])
        await _until(lambda: manager.in_flight == 0)
        await manager.stop()

        await env.app.stats.flush()
        assert await env.app.db.fetchval(q.MEMBER_COUNT_IN_CHAT, BOT_ID, CHAT_ID, 5) == 2
        last = [m for m in env.session.sent if isinstance(m, GetUpdates)][-1]
        assert (last.offset, last.timeout) == (12, 0)
        assert last.allowed_updates == ALLOWED_UPDATES

    scenario(body)


def test_clone_runs_with_separate_data_and_encrypted_token():
    async def body(env: Env) -> None:
        manager = _manager(env)
        await manager.start(TOKEN)
        me = await manager.add_clone(CLONE_TOKEN, owner_id=42)
        assert me.id == CLONE_ID and manager.running_count == 2

        row = await env.app.db.fetchrow(
            "SELECT owner_id, token_enc, is_main FROM bots WHERE id = $1", CLONE_ID
        )
        assert row["owner_id"] == 42 and not row["is_main"]
        assert CLONE_TOKEN.encode() not in bytes(row["token_enc"])

        env.session.updates[CLONE_ID] = [_raw_update(1, CHAT_ID)]
        await _until(lambda: not env.session.updates[CLONE_ID] and manager.in_flight == 0)
        await _until(lambda: env.app.stats.pending > 0)
        await manager.stop()
        await env.app.stats.flush()

        assert await env.app.db.fetchval(q.MEMBER_COUNT_IN_CHAT, CLONE_ID, CHAT_ID, 5) == 1
        assert await env.app.db.fetchval(q.MEMBER_COUNT_IN_CHAT, BOT_ID, CHAT_ID, 5) is None
        assert await env.app.chats.count_active(CLONE_ID) == 1
        assert await env.app.chats.count_active(BOT_ID) == 0

    scenario(body)


def test_clone_restarts_after_restart_unless_removed():
    async def body(env: Env) -> None:
        first = _manager(env)
        await first.start(TOKEN)
        await first.add_clone(CLONE_TOKEN, owner_id=42)
        await first.stop()

        second = _manager(env)
        await second.start(TOKEN)
        assert second.running_count == 2
        await second.remove_clone(CLONE_ID)
        assert second.running_count == 1
        await second.stop()

        third = _manager(env)
        await third.start(TOKEN)
        assert third.running_count == 1
        await third.stop()

    scenario(body)


def test_clone_add_rejects_bad_requests():
    async def body(env: Env) -> None:
        manager = _manager(env)
        await manager.start(TOKEN)
        with pytest.raises(CloneError, match="не похоже"):
            await manager.add_clone("это не токен", owner_id=42)
        with pytest.raises(CloneError, match="уже подключён"):
            await manager.add_clone(TOKEN, owner_id=42)
        env.session.revoked.add(CLONE_ID)
        with pytest.raises(CloneError, match="не принял"):
            await manager.add_clone(CLONE_TOKEN, owner_id=42)
        with pytest.raises(CloneError, match="Основного"):
            await manager.remove_clone(BOT_ID)
        assert manager.running_count == 1
        await manager.stop()

    scenario(body)


def test_clones_need_secret_key():
    async def body(env: Env) -> None:
        manager = _manager(env, secret_key=None)
        await manager.start(TOKEN)
        with pytest.raises(CloneError, match="SECRET_KEY"):
            await manager.add_clone(CLONE_TOKEN, owner_id=42)
        await manager.stop()

    scenario(body)


def test_revoked_clone_stops_alone_and_is_marked():
    async def body(env: Env) -> None:
        manager = _manager(env)
        await manager.start(TOKEN)
        await manager.add_clone(CLONE_TOKEN, owner_id=42)
        env.session.revoked.add(CLONE_ID)
        await _until(lambda: manager.running_count == 1)
        status = await env.app.db.fetchval("SELECT status FROM bots WHERE id = $1", CLONE_ID)
        assert status == "revoked"
        assert manager.is_main(BOT_ID)
        await manager.stop()

    scenario(body)


def test_revoked_main_token_asks_process_to_stop():
    async def body(env: Env) -> None:
        stopped = asyncio.Event()
        manager = _manager(env, on_main_fatal=stopped.set)
        await manager.start(TOKEN)
        env.session.revoked.add(BOT_ID)
        await asyncio.wait_for(stopped.wait(), 3)
        await manager.stop()

    scenario(body)


def test_wrong_main_token_fails_at_start():
    async def body(env: Env) -> None:
        env.session.revoked.add(BOT_ID)
        with pytest.raises(TelegramUnauthorizedError):
            await _manager(env).start(TOKEN)

    scenario(body)


def test_entry_point_reports_missing_settings(monkeypatch, tmp_path, capsys):
    from bot.__main__ import main

    for name in ("BOT_TOKEN", "OWNER_IDS", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    assert main() == 2
    assert "BOT_TOKEN" in capsys.readouterr().err
