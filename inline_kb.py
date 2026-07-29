"""
Inline-кнопки для взаимодействия в боте.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import get_text


def get_order_buttons(user_id: int) -> InlineKeyboardMarkup:
    """Кнопки под заявкой в группе."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{get_text('info_button')}",
                    callback_data=f"order_info_{user_id}"
                ),
                InlineKeyboardButton(
                    text=f"{get_text('history_button')}",
                    callback_data=f"order_history_{user_id}"
                ),
                InlineKeyboardButton(
                    text=f"{get_text('block_button')}",
                    callback_data=f"order_block_{user_id}"
                ),
            ],
        ]
    )
    return keyboard


def get_user_history_button(user_id: int) -> InlineKeyboardMarkup:
    """Кнопка для просмотра истории пользователя."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{get_text('user_history_button')}",
                    callback_data=f"admin_user_history_{user_id}"
                ),
            ],
        ]
    )
    return keyboard
