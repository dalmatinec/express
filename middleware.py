"""
Антифлуд и антиспам для пользователей.
Подключается ТОЛЬКО к пользовательскому роутеру (личка с ботом),
поэтому на рабочую группу ограничения не действуют.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Any, Awaitable, Optional

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from config import (
    FLOOD_MIN_INTERVAL, FLOOD_WINDOW, FLOOD_MAX_MESSAGES,
    FLOOD_MUTE_SECONDS, DUPLICATE_WINDOW,
)
from filters import is_admin
from texts import render

WARN_COOLDOWN = 10  # не чаще одного предупреждения за столько секунд


@dataclass
class UserState:
    history: deque = field(default_factory=deque)  # время всех попыток за окно
    last_time: float = 0.0
    muted_until: float = 0.0
    last_text: Optional[str] = None
    last_text_time: float = 0.0
    last_warn: float = 0.0


class AntiFloodMiddleware(BaseMiddleware):
    """
    - минимальный интервал между сообщениями;
    - лимит сообщений за окно, при превышении — временный мут;
    - повтор одного и того же текста игнорируется.
    Администраторы бота ограничений не имеют.
    """

    def __init__(self):
        super().__init__()
        self.users: dict[int, UserState] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        user_id = event.from_user.id
        if is_admin(user_id):
            return await handler(event, data)

        now = time.monotonic()
        self._cleanup(now)
        state = self.users.setdefault(user_id, UserState())

        # Мут — молча игнорируем
        if now < state.muted_until:
            return None

        # Считаем все попытки, в том числе отброшенные: спам ведёт к муту
        state.history.append(now)
        while state.history and now - state.history[0] > FLOOD_WINDOW:
            state.history.popleft()

        too_fast = now - state.last_time < FLOOD_MIN_INTERVAL
        state.last_time = now

        if len(state.history) > FLOOD_MAX_MESSAGES:
            state.muted_until = now + FLOOD_MUTE_SECONDS
            state.history.clear()
            await self._warn(event, state, now, "muted", force=True,
                             minutes=max(1, round(FLOOD_MUTE_SECONDS / 60)))
            return None

        if too_fast:
            await self._warn(event, state, now, "flood")
            return None

        if event.text and event.text == state.last_text and now - state.last_text_time < DUPLICATE_WINDOW:
            await self._warn(event, state, now, "duplicate")
            return None

        if event.text:
            state.last_text = event.text
            state.last_text_time = now

        return await handler(event, data)

    async def _warn(self, message: Message, state: UserState, now: float, key: str,
                    force: bool = False, **params):
        if not force and now - state.last_warn < WARN_COOLDOWN:
            return
        state.last_warn = now
        text = render(key, **params)
        if text:
            await message.answer(text)

    def _cleanup(self, now: float):
        """Не даём словарю бесконечно расти."""
        if len(self.users) < 10_000:
            return
        ttl = max(FLOOD_WINDOW, DUPLICATE_WINDOW, FLOOD_MUTE_SECONDS)
        for uid in [uid for uid, s in self.users.items()
                    if now - s.last_time > ttl and now > s.muted_until]:
            del self.users[uid]
