"""Действия в Telegram: ошибки API переводятся в понятные коды."""
from __future__ import annotations

from collections.abc import Awaitable
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import ChatPermissions

from .service import ModerationError, classify_telegram_error

# Заполняем по списку полей: новое право в Telegram не потребует менять этот код.
_ALL_FIELDS = tuple(ChatPermissions.model_fields)
MUTED = ChatPermissions(**dict.fromkeys(_ALL_FIELDS, False))
# По документации Telegram все True снимают ограничения с участника.
FREE = ChatPermissions(**dict.fromkeys(_ALL_FIELDS, True))

# Минимальный срок бана: Telegram считает срок короче 30 секунд вечным.
_KICK_BAN_SECONDS = 35


async def _call[T](request: Awaitable[T]) -> T:
    try:
        return await request
    except TelegramRetryAfter as exc:
        raise ModerationError("flood") from exc
    except TelegramForbiddenError as exc:
        raise ModerationError("bot_no_rights") from exc
    except TelegramBadRequest as exc:
        raise ModerationError(classify_telegram_error(exc.message)) from exc
    except TelegramAPIError as exc:
        raise ModerationError("tg_error") from exc


class AiogramActions:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self.bot_id = bot.id

    async def ban(self, chat_id: int, user_id: int, until: datetime | None) -> None:
        await _call(self._bot.ban_chat_member(chat_id, user_id, until_date=until))

    async def unban(self, chat_id: int, user_id: int) -> None:
        await _call(self._bot.unban_chat_member(chat_id, user_id, only_if_banned=True))

    async def mute(self, chat_id: int, user_id: int, until: datetime | None) -> None:
        await _call(self._bot.restrict_chat_member(chat_id, user_id, MUTED, until_date=until))

    async def unmute(self, chat_id: int, user_id: int) -> None:
        await _call(self._bot.restrict_chat_member(chat_id, user_id, FREE))

    async def kick(self, chat_id: int, user_id: int) -> None:
        # В Telegram нет кика: это бан с немедленным разбаном. Короткий срок бана страхует
        # от случая, когда разбан не прошёл: человек не останется заблокированным навсегда.
        until = datetime.now(UTC) + timedelta(seconds=_KICK_BAN_SECONDS)
        await _call(self._bot.ban_chat_member(chat_id, user_id, until_date=until))
        with suppress(TelegramAPIError):
            await self._bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
