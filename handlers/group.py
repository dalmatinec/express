"""
Рабочая группа: ответ реплаем на пересланное сообщение уходит пользователю.
Ограничений по количеству/частоте ответов нет — антифлуд сюда не подключается.
Команды /ban, /unban и управление админами — в handlers/admin.py (только для админов бота).
"""

import logging
from typing import Optional

from aiogram import Router
from aiogram.types import Message, ReactionTypeEmoji

from database import db
from filters import IsWorkGroup
from handlers.common import with_retry
from texts import render

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsWorkGroup())

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


def author_of(message: Message) -> str:
    """@username отвечающего, а если его нет — ID."""
    # Анонимный админ группы пишет от имени чата
    sender = message.sender_chat or message.from_user
    if sender.username:
        return "@" + sender.username
    return f"<code>{sender.id}</code>"


def linked_user(message: Message) -> Optional[int]:
    """Пользователь, чьё пересланное сообщение процитировано реплаем."""
    original = message.reply_to_message
    if original is None or original.from_user is None or original.from_user.id != message.bot.id:
        return None
    return db.get_linked_user(message.chat.id, original.message_id)


# ==================== ОТВЕТЫ ПОЛЬЗОВАТЕЛЯМ ====================

@router.message()
async def group_reply(message: Message):
    user_id = linked_user(message)
    if user_id is None:
        return  # обычная переписка в группе — не трогаем

    # Команды, адресованные не боту, пользователю не отправляем
    if message.text and message.text.startswith("/"):
        return

    author = author_of(message)
    body = message.html_text if (message.text or message.caption) else ""
    full = render("reply", author=author, text=body).strip() or body

    try:
        if message.text and len(full) <= TEXT_LIMIT:
            await with_retry(lambda: message.bot.send_message(user_id, full))
        elif message.caption is not None or message.photo or message.video or message.document \
                or message.audio or message.animation or message.voice:
            if len(full) <= CAPTION_LIMIT:
                await with_retry(lambda: message.copy_to(user_id, caption=full))
            else:
                await send_header_and_copy(message, user_id, author)
        else:
            # Длинный текст, стикеры, кружки, геолокации и т.п. — отдельно заголовок и само сообщение
            await send_header_and_copy(message, user_id, author)
    except Exception as e:
        logger.warning("Не удалось доставить ответ пользователю %s: %s", user_id, e)
        await message.reply(render("delivery_failed", user_id=user_id) or "❌ Не доставлено.")
        return

    try:
        await message.react([ReactionTypeEmoji(emoji="👍")])
    except Exception:
        pass  # реакции могут быть отключены в группе — не критично


async def send_header_and_copy(message: Message, user_id: int, author: str):
    header = render("reply", author=author, text="").strip()
    if header:
        await with_retry(lambda: message.bot.send_message(user_id, header))
    await with_retry(lambda: message.copy_to(user_id))
