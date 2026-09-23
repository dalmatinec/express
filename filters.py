"""
Фильтры и общие проверки: администратор, рабочая группа.
"""

from typing import Optional

from aiogram.filters import BaseFilter
from aiogram.types import Message, TelegramObject

from config import SUPER_ADMIN_ID, DEFAULT_GROUP_ID
from database import db


def is_super_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID


def is_admin(user_id: int) -> bool:
    return is_super_admin(user_id) or db.is_admin(user_id)


def get_group_id() -> Optional[int]:
    """ID рабочей группы: из БД, иначе из конфига."""
    value = db.get_setting("group_id")
    if value:
        try:
            return int(value)
        except ValueError:
            pass
    return DEFAULT_GROUP_ID


class IsAdmin(BaseFilter):
    """Для сообщений и нажатий кнопок."""
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and is_admin(user.id)


class IsSuperAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and is_super_admin(user.id)


class IsWorkGroup(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        group_id = get_group_id()
        return group_id is not None and message.chat.id == group_id
