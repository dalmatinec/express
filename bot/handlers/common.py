"""Базовые команды и служебные события чатов."""
from __future__ import annotations

import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import ChatMemberUpdated, Message

from ..db import queries as q
from ..metrics import rss_mb
from ..texts import t

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}
IN_CHAT = {"member", "administrator", "creator", "restricted"}
ADMIN = {"administrator", "creator"}


def _format_uptime(seconds: float) -> str:
    minutes = int(seconds) // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


# В личку сюда попадают только владельцы: остальных отсекает PrivateGateMiddleware.
async def start(message: Message) -> None:
    await message.answer(t("start"))


async def ping(message: Message, bot: Bot, app: App) -> None:
    if message.chat.type in GROUP_TYPES:
        # В группе отвечаем только админам, чтобы команду не использовали для шума.
        if message.sender_chat is None or message.sender_chat.id != message.chat.id:
            author = message.from_user
            if author is None or await app.admins.get(bot, message.chat.id, author.id) is None:
                return
    await message.reply(t("ping"))


async def me(message: Message, bot: Bot, app: App) -> None:
    author = message.from_user
    if author is None or author.is_bot:
        return
    # Показываем свежие цифры, а не состояние на последнюю запись пачки.
    await app.stats.flush()
    totals = await app.db.fetchrow(q.USER_TOTALS, bot.id, author.id)
    if message.chat.type in GROUP_TYPES:
        here = await app.db.fetchval(q.MEMBER_COUNT_IN_CHAT, bot.id, message.chat.id, author.id)
        text = t(
            "me",
            name=author.full_name,
            here=here or 0,
            total=totals["messages"],
            chats=totals["chats"],
        )
    else:
        text = t(
            "me_private", name=author.full_name, total=totals["messages"], chats=totals["chats"]
        )
    await message.reply(text)


async def status(message: Message, bot: Bot, app: App) -> None:
    if message.from_user is None or message.from_user.id not in app.settings.owner_ids:
        return
    cache = app.chats.cache
    lookups = cache.hits + cache.misses
    rss = rss_mb()
    await message.answer(
        t(
            "status",
            running=app.manager.running_count if app.manager else 0,
            chats=await app.chats.count_active(bot.id),
            in_flight=app.manager.in_flight if app.manager else 0,
            pending=app.stats.pending,
            rss=f"{rss:.0f} МБ" if rss is not None else "нет данных",
            cache_hit=round(cache.hits * 100 / lookups) if lookups else 0,
            cache_size=len(cache),
            uptime=_format_uptime(time.monotonic() - app.started_at),
        )
    )


async def migrated(message: Message, bot: Bot, app: App) -> None:
    new_id = message.migrate_to_chat_id
    if new_id is None:
        return
    # Сначала дописываем счётчики на старый id, чтобы они переехали вместе с остальными.
    await app.stats.flush()
    await app.chats.migrate(bot.id, message.chat.id, new_id)
    app.admins.invalidate(bot.id, message.chat.id)
    log.info("Чат %s стал супергруппой %s", message.chat.id, new_id)


async def bot_status_changed(event: ChatMemberUpdated, bot: Bot, app: App) -> None:
    chat = event.chat
    if chat.type not in GROUP_TYPES:
        return
    old, new = event.old_chat_member.status, event.new_chat_member.status
    app.admins.invalidate(bot.id, chat.id)
    if new not in IN_CHAT:
        await app.chats.set_active(bot.id, chat.id, False)
        return
    await app.chats.touch(bot.id, chat.id, chat.type, chat.title, chat.username)
    text = None
    if old not in IN_CHAT:
        text = t("promoted") if new in ADMIN else t("added")
    elif new in ADMIN and old not in ADMIN:
        text = t("promoted")
    elif old in ADMIN and new not in ADMIN:
        text = t("demoted")
    if text is not None:
        with suppress(TelegramAPIError):  # боту могли запретить писать в чат
            await bot.send_message(chat.id, text)


async def member_changed(event: ChatMemberUpdated, app: App, bot: Bot) -> None:
    old, new = event.old_chat_member, event.new_chat_member
    if old.status in ADMIN or new.status in ADMIN:
        app.admins.invalidate(bot.id, event.chat.id)


def build_router() -> Router:
    router = Router(name="common")
    router.message.register(start, CommandStart(), F.chat.type == "private")
    router.message.register(ping, Command("ping"))
    router.message.register(me, Command("me"))
    router.message.register(status, Command("status"), F.chat.type == "private")
    router.message.register(migrated, F.migrate_to_chat_id)
    router.my_chat_member.register(bot_status_changed)
    router.chat_member.register(member_changed)
    return router
