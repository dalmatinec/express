"""
Общие помощники для обработчиков.
"""

import asyncio
import html
import logging
from typing import Awaitable, Callable, TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from config import SUPER_ADMIN_IDS
from database import db

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retry(call: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    """Повторить запрос к Telegram, если упёрлись в лимит (429 Too Many Requests)."""
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramRetryAfter as e:
            if attempt == attempts - 1:
                raise
            logger.warning("Лимит Telegram, ждём %s сек.", e.retry_after)
            await asyncio.sleep(e.retry_after)


# ==================== ОБЩЕЕ ДЛЯ АДМИНКИ ====================

def format_person(user_id: int, name: str = "", username: str = "") -> str:
    """«Имя (@username) — ID», чтобы было понятно, чей это ID."""
    parts = [html.escape(name) if name else "Без имени"]
    if username:
        parts.append(f"(@{username})")
    return f"{' '.join(parts)} — <code>{user_id}</code>"


async def person_info(bot: Bot, user_id: int) -> tuple[str, str]:
    """Имя и username по ID: из Telegram, иначе из базы."""
    try:
        chat = await bot.get_chat(user_id)
        name = " ".join(filter(None, [chat.first_name, chat.last_name])) or (chat.title or "")
        return name, chat.username or ""
    except Exception:
        pass
    known = db.get_user(user_id) or db.get_admin(user_id) or {}
    name = known.get("name") or " ".join(filter(None, [known.get("first_name"), known.get("last_name")]))
    return name or "", known.get("username") or ""


async def refreshed_admins(bot: Bot) -> list[dict]:
    """Админы из базы с актуальными именами (имя в Telegram могло поменяться)."""
    admins = []
    for admin in db.get_admins():
        name, username = await person_info(bot, admin["admin_id"])
        name, username = name or admin["name"] or "", username or admin["username"] or ""
        if (name, username) != (admin["name"] or "", admin["username"] or ""):
            db.add_admin(admin["admin_id"], name, username)
        admins.append({"admin_id": admin["admin_id"], "name": name, "username": username})
    return admins


async def admins_text(bot: Bot) -> str:
    lines = [f"👑 {format_person(sid, *await person_info(bot, sid))}" for sid in SUPER_ADMIN_IDS]
    for admin in await refreshed_admins(bot):
        lines.append(f"👮 {format_person(admin['admin_id'], admin['name'], admin['username'])}")
    return "<b>👮 Администраторы</b>\n\n" + "\n".join(lines)


async def apply_group(bot: Bot, group_id: int) -> str:
    """Сделать группу рабочей, если бот её видит. Возвращает текст результата."""
    try:
        chat = await bot.get_chat(group_id)
    except Exception as e:
        return f"❌ Бот не видит группу <code>{group_id}</code>: {html.escape(str(e))}"
    db.set_setting("group_id", str(group_id))
    return f"✅ Рабочая группа: <b>{html.escape(chat.title or '')}</b> (<code>{group_id}</code>)"
