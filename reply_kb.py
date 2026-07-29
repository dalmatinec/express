"""
Reply-клавиатуры (нижняя клавиатура для пользователей).
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from utils import get_text


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура для пользователя."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"📝 {get_text('order_button')}")],
            [KeyboardButton(text=f"🏆 {get_text('top_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    return keyboard


def get_admin_main_keyboard() -> ReplyKeyboardMarkup:
    """Главная клавиатура для администратора."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👋 Приветственное сообщение")],
            [KeyboardButton(text="🏆 Топ недели")],
            [KeyboardButton(text="👮 Администраторы")],
            [KeyboardButton(text="👥 Рабочая группа")],
            [KeyboardButton(text="🔎 Информация о пользователе")],
            [KeyboardButton(text="📊 Статистика")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    return keyboard


def get_back_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой назад."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{get_text('back_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    return keyboard


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{get_text('cancel_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    return keyboard


def get_greeting_submenu_keyboard() -> ReplyKeyboardMarkup:
    """Подменю для приветственного сообщения."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Изменить текст")],
            [KeyboardButton(text="🖼 Изменить изображение")],
            [KeyboardButton(text="❌ Удалить изображение")],
            [KeyboardButton(text="👁 Просмотреть текущее сообщение")],
            [KeyboardButton(text=f"{get_text('back_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    return keyboard


def get_top_submenu_keyboard() -> ReplyKeyboardMarkup:
    """Подменю для топа недели."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Изменить сообщение")],
            [KeyboardButton(text="👁 Просмотреть сообщение")],
            [KeyboardButton(text=f"{get_text('back_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    return keyboard


def get_admins_submenu_keyboard() -> ReplyKeyboardMarkup:
    """Подменю для администраторов."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить администратора")],
            [KeyboardButton(text="➖ Удалить администратора")],
            [KeyboardButton(text="📋 Список администраторов")],
            [KeyboardButton(text=f"{get_text('back_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    return keyboard


def get_group_submenu_keyboard() -> ReplyKeyboardMarkup:
    """Подменю для рабочей группы."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Изменить ID группы")],
            [KeyboardButton(text="👁 Посмотреть текущий ID")],
            [KeyboardButton(text="✅ Проверить подключение")],
            [KeyboardButton(text=f"{get_text('back_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    return keyboard


def get_stats_submenu_keyboard() -> ReplyKeyboardMarkup:
    """Подменю для статистики."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Пользователи")],
            [KeyboardButton(text="📨 Обращения")],
            [KeyboardButton(text="🚫 Заблокированные")],
            [KeyboardButton(text="👮 Администраторы")],
            [KeyboardButton(text=f"{get_text('back_button')}")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    return keyboard
