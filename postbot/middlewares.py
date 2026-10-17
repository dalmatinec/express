"""Пускаем в личку только владельцев и админов. Чужим бот отвечает один раз и дальше молчит."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .app import App


class AccessMiddleware(BaseMiddleware):
    def __init__(self, app: App) -> None:
        self.app = app
        self.told: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # всплывающие кнопки в постах нажимают обычные подписчики
        if isinstance(event, CallbackQuery) and (event.data or "").startswith("pp:"):
            return await handler(event, data)
        chat = data.get("event_chat")
        if chat is not None and chat.type != "private":
            return await handler(event, data)
        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return None
        if self.app.role(user.id) is not None:
            return await handler(event, data)
        try:
            if isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            elif isinstance(event, Message) and user.id not in self.told:
                self.told.add(user.id)
                await event.answer("⛔️ Этот бот только для администраторов.\n"
                                   f"Ваш ID: <code>{user.id}</code>, его можно передать владельцу.")
        except TelegramAPIError:
            pass
        return None
