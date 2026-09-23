"""
Личка с ботом: всё, что пишет пользователь (включая /start), пересылается в рабочую группу.
Принимаются только текстовые сообщения. Антифлуд подключается к этому роутеру в bot.py.
"""

import html
import logging

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import MAX_TEXT_LENGTH
from database import db
from filters import get_group_id
from handlers.common import with_retry
from texts import render

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")


async def answer(message: Message, key: str, **params):
    """Ответить пользователю текстом по ключу (если текст не отключён)."""
    text = render(key, **params)
    if text:
        await message.answer(text)


async def forward_to_group(message: Message) -> bool:
    """Переслать сообщение пользователя в группу и запомнить связь для ответов."""
    group_id = get_group_id()
    if group_id is None:
        logger.error("Рабочая группа не настроена — используйте /setgroup")
        return False

    try:
        forwarded = await with_retry(lambda: message.forward(chat_id=group_id))
    except Exception as e:
        logger.error("Не удалось переслать сообщение %s в группу %s: %s", message.from_user.id, group_id, e)
        return False

    db.save_link(group_id, forwarded.message_id, message.from_user.id)
    return True


def remember_user(message: Message):
    user = message.from_user
    db.upsert_user(user.id, user.first_name or "", user.last_name or "", user.username or "")


@router.message(CommandStart())
async def start_command(message: Message):
    """/start тоже пересылается в группу, затем пользователю приходит приветствие."""
    remember_user(message)

    if db.is_banned(message.from_user.id):
        await answer(message, "banned")
        return

    await forward_to_group(message)

    await answer(
        message, "welcome",
        first_name=html.escape(message.from_user.first_name or ""),
        last_name=html.escape(message.from_user.last_name or ""),
    )


@router.message(F.text)
async def user_text(message: Message):
    remember_user(message)

    if db.is_banned(message.from_user.id):
        await answer(message, "banned")
        return

    if len(message.text) > MAX_TEXT_LENGTH:
        await answer(message, "too_long", max=MAX_TEXT_LENGTH)
        return

    if await forward_to_group(message):
        await answer(message, "sent")
    else:
        await answer(message, "unavailable")


@router.message()
async def user_not_text(message: Message):
    """Фото, стикеры, голосовые и т.д. не принимаются."""
    if db.is_banned(message.from_user.id):
        return
    await answer(message, "only_text")
