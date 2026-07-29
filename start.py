"""
Обработка /start и приветственного сообщения.
"""

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from database import db
from utils import get_text, format_start_message, load_texts
from reply_kb import get_main_keyboard

router = Router()


@router.message(Command("start"))
async def start_command(message: Message):
    """Обработка команды /start."""
    user_id = message.from_user.id
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    username = message.from_user.username or ""

    # Добавить или обновить пользователя в БД
    db.add_or_update_user(user_id, first_name, last_name, username)

    # Получить персонализованное сообщение приветствия
    start_setting = db.get_setting('start_message')

    if start_setting and start_setting.get('value'):
        # Использовать кастомное сообщение из БД
        welcome_text = start_setting['value'].format(
            first_name=first_name,
            last_name=last_name
        )
        photo_file_id = start_setting.get('photo_file_id')
    else:
        # Использовать стандартное сообщение
        welcome_text = format_start_message(first_name, last_name)
        photo_file_id = None

    keyboard = get_main_keyboard()

    # Отправить сообщение с фото или без
    if photo_file_id:
        await message.answer_photo(
            photo=photo_file_id,
            caption=welcome_text,
            reply_markup=keyboard
        )
    else:
        await message.answer(
            text=welcome_text,
            reply_markup=keyboard
        )


@router.message(F.text == "🏆 Топ недели")
async def show_top_week(message: Message):
    """Показать сообщение топа недели."""
    top_setting = db.get_setting('top_message')

    if top_setting and top_setting.get('value'):
        top_text = top_setting['value']
    else:
        top_text = get_text('top_default')

    await message.answer(text=top_text)
