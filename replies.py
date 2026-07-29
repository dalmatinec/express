"""
Обработка ответов от продавцов в рабочей группе.
Только Reply-сообщения отправляются пользователю.
"""

from aiogram import Router, F
from aiogram.types import Message
from database import db
from utils import is_text_message, format_reply_header, get_text

router = Router()


@router.message()
async def handle_group_reply(message: Message):
    """
    Обработка сообщений в рабочей группе.
    Только Reply на заявку бота отправляются пользователю.
    """

    # Проверить, что это Reply
    if not message.reply_to_message:
        return

    # Проверить, что это текстовое сообщение
    if not is_text_message(message):
        await message.answer(get_text('no_text_in_group'))
        return

    # Получить исходное сообщение (заявку)
    original_message = message.reply_to_message

    # Проверить, отправил ли это сообщение сам бот
    if original_message.from_user.id != message.bot.id:
        return

    # Найти заявку по message_id
    order = db.get_order_by_message_id(original_message.message_id)

    if not order:
        return

    # Получить информацию о продавце
    seller_first_name = message.from_user.first_name or ""
    seller_last_name = message.from_user.last_name or ""
    seller_username = message.from_user.username or ""

    # Получить User ID пользователя, отправившего заявку
    user_id = order['user_id']

    # Форматировать ответ
    reply_header = format_reply_header(seller_first_name, seller_last_name, seller_username)

    full_reply = reply_header + message.text

    # Отправить ответ пользователю
    try:
        await message.bot.send_message(
            chat_id=user_id,
            text=full_reply
        )
    except Exception as e:
        print(f"Ошибка при отправке ответа пользователю {user_id}: {e}")
