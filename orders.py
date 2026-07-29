"""
Приём заказов от пользователей и отправка в группу.
"""

from aiogram import Router, F
from aiogram.types import Message
from database import db
from utils import (
    get_text, is_text_message, format_order_header,
    format_date_time, format_datetime_string
)
from inline_kb import get_order_buttons
from reply_kb import get_main_keyboard

router = Router()


@router.message(F.text.contains("📝"))
async def handle_order_button(message: Message):
    """Обработка нажатия кнопки 'Сделать заказ'."""
    await message.answer(
        text="Отправьте ваш запрос текстовым сообщением:",
        reply_markup=get_main_keyboard()
    )


@router.message()
async def receive_order(message: Message):
    """
    Получить заказ от пользователя.
    Поддерживает только текстовые сообщения.
    """
    user_id = message.from_user.id

    # Проверить, заблокирован ли пользователь
    if db.is_user_blocked(user_id):
        await message.answer(get_text('blocked_user'))
        return

    # Проверить, что это текстовое сообщение
    if not is_text_message(message):
        await message.answer(get_text('only_text'))
        return

    # Получить информацию о пользователе
    user = db.get_user(user_id)
    first_name = user['first_name'] if user else message.from_user.first_name or ""
    last_name = user['last_name'] if user else message.from_user.last_name or ""
    username = user['username'] if user else message.from_user.username or ""

    # Сохранить заказ в БД
    order_id = db.save_order(user_id, message.text)

    # Получить ID рабочей группы
    group_setting = db.get_setting('group_id')
    group_id = None
    if group_setting and group_setting.get('value'):
        try:
            group_id = int(group_setting['value'])
        except (ValueError, TypeError):
            pass

    if group_id:
        try:
            # Форматировать сообщение для группы
            order_header = format_order_header(first_name, last_name, username)
            order_header = order_header.replace("{}", str(user_id))

            full_message = order_header + message.text

            # Отправить в группу с кнопками
            group_message = await message.bot.send_message(
                chat_id=group_id,
                text=full_message,
                reply_markup=get_order_buttons(user_id)
            )

            # Сохранить message_id для связи ответов
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE orders SET message_id_in_group = ? WHERE order_id = ?",
                (group_message.message_id, order_id)
            )
            conn.commit()
            conn.close()

        except Exception as e:
            print(f"Ошибка при отправке в группу: {e}")

    # Отправить подтверждение пользователю
    await message.answer(get_text('order_sent'), reply_markup=get_main_keyboard())
