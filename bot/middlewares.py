"""Учёт на каждом событии. Код без обращений к базе, кроме первой встречи с чатом."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from .chats import ChatRepo
from .metrics import is_countable
from .moderation.service import Person
from .moderation.telegram import AiogramActions
from .protection.service import Incoming, Protection
from .protection.telegram import AiogramMessenger, view_of
from .stats import StatsBuffer

GROUP_TYPES = frozenset({"group", "supergroup"})


class TrackingMiddleware(BaseMiddleware):
    def __init__(self, chats: ChatRepo, stats: StatsBuffer) -> None:
        self._chats = chats
        self._stats = stats

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and not user.is_bot:
            self._stats.touch_user(user.id, user.username, user.first_name)

        if isinstance(event, Update) and event.message is not None:
            message = event.message
            chat = message.chat
            if chat.type in GROUP_TYPES and message.migrate_to_chat_id is None:
                bot_id = data["bot"].id
                await self._chats.touch(bot_id, chat.id, chat.type, chat.title, chat.username)
                author = message.from_user
                if author is not None and is_countable(
                    message.content_type,
                    is_bot=author.is_bot,
                    has_sender_chat=message.sender_chat is not None,
                ):
                    self._stats.add_message(bot_id, chat.id, author.id, message.date)
        return await handler(event, data)


class PrivateGateMiddleware(BaseMiddleware):
    """Личка бота пока только для владельца: остальным бот в личке не отвечает вообще."""

    def __init__(self, owner_ids: frozenset[int]) -> None:
        self._owner_ids = owner_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat")
        if chat is not None and chat.type == "private":
            user = data.get("event_from_user")
            if user is None or user.id not in self._owner_ids:
                return None
        return await handler(event, data)


class ProtectionMiddleware(BaseMiddleware):
    """Антифлуд и антиспам. На обычном сообщении работает без базы и обращений к Telegram."""

    def __init__(self, chats: ChatRepo, protection: Protection) -> None:
        self._chats = chats
        self._protection = protection

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and event.message is not None:
            message = event.message
            author = message.from_user
            # Посты от имени чата или канала и ботов не проверяем: у них нет человека-автора.
            if (
                message.chat.type in GROUP_TYPES
                and author is not None
                and not author.is_bot
                and message.sender_chat is None
                and message.migrate_to_chat_id is None
            ):
                bot = data["bot"]
                info = await self._chats.get(bot.id, message.chat.id)
                if info is not None:
                    removed = await self._protection.inspect(
                        bot,
                        AiogramActions(bot),
                        AiogramMessenger(bot),
                        info.settings,
                        Incoming(
                            chat_id=message.chat.id,
                            chat_username=message.chat.username,
                            author=Person(author.id, author.full_name, author.username),
                            message_id=message.message_id,
                            thread_id=message.message_thread_id,
                            album=message.media_group_id,
                            view=view_of(message),
                        ),
                    )
                    if removed:
                        return None
        return await handler(event, data)
