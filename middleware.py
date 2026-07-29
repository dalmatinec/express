"""
Middleware системы.
Проверка администраторов, антифлуд, работа с временем и т.д.
"""

from typing import Callable, Any, Awaitable
from datetime import datetime, timedelta
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from config import SUPER_ADMIN_ID
from database import db
from utils import get_kazakhstan_time, get_text


class AdminCheckMiddleware(BaseMiddleware):
    """Middleware для проверки прав администратора."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            user_id = event.from_user.id

            # Проверяем, админ ли пользователь
            is_admin = db.is_admin(user_id, SUPER_ADMIN_ID)
            data['is_admin'] = is_admin
            data['is_super_admin'] = (user_id == SUPER_ADMIN_ID)

        return await handler(event, data)


class AntiFloodMiddleware(BaseMiddleware):
    """Middleware для защиты от флуда."""

    def __init__(self):
        super().__init__()
        self.user_last_message = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            user_id = event.from_user.id

            # Администраторы не подлежат ограничениям
            if data.get('is_admin') or data.get('is_super_admin'):
                return await handler(event, data)

            # Проверяем антифлуд
            now = datetime.now()
            if user_id in self.user_last_message:
                last_time = self.user_last_message[user_id]
                delta = (now - last_time).total_seconds()

                if delta < 5:  # 5 секунд
                    # Отправляем сообщение об ограничении
                    await event.answer(get_text('flood_message'))
                    return

            self.user_last_message[user_id] = now

        return await handler(event, data)


class TimezoneMiddleware(BaseMiddleware):
    """Middleware для работы с часовым поясом Казахстана."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data['kz_now'] = get_kazakhstan_time()
        return await handler(event, data)
