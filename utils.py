"""
Вспомогательные функции.
Форматирование даты/времени, работа с текстами и т.д.
"""

import json
from datetime import datetime, timedelta
from typing import Dict, Any
from aiogram.types import Message


def load_texts() -> Dict[str, Any]:
    """Загрузить все тексты из texts.json."""
    try:
        with open('texts.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError("texts.json не найден!")


def get_text(key: str, default: str = None) -> str:
    """Получить текст по ключу."""
    texts = load_texts()
    if key in texts.get('texts', {}):
        return texts['texts'][key]
    return default or f"[{key}]"


def get_emoji(name: str, default: str = "") -> str:
    """Получить Premium Emoji по названию."""
    texts = load_texts()
    emojis = texts.get('premium_emojis', {})
    return emojis.get(name, default)


def get_kazakhstan_time() -> datetime:
    """Получить текущее время в часовом поясе Казахстана (UTC+6)."""
    utc_time = datetime.utcnow()
    kz_time = utc_time + timedelta(hours=6)
    return kz_time


def format_date_time(dt: datetime = None) -> tuple:
    """Форматировать дату и время для казахстанского часового пояса."""
    if dt is None:
        dt = get_kazakhstan_time()

    date_str = dt.strftime("%d.%m.%Y")
    time_str = dt.strftime("%H:%M")
    return date_str, time_str


def format_datetime_string(dt: datetime = None) -> str:
    """Форматировать дату и время в одну строку."""
    if dt is None:
        dt = get_kazakhstan_time()

    return dt.strftime("%d.%m.%Y • %H:%M")


def format_start_message(first_name: str, last_name: str = "") -> str:
    """Форматировать стартовое сообщение с именем пользователя."""
    texts = load_texts()
    template = texts.get('start_message_default', '')

    last_name = last_name or ""
    return template.format(first_name=first_name, last_name=last_name)


def is_text_message(message: Message) -> bool:
    """Проверить, является ли сообщение текстовым."""
    return message.text is not None and message.text.strip() != ""


def escape_markdown_v2(text: str) -> str:
    """Экранировать спецсимволы для MarkdownV2."""
    chars = r'_*[\]()~`>#+-=|{}.!'
    for char in chars:
        text = text.replace(char, f'\\{char}')
    return text


def truncate_text(text: str, max_length: int = 100) -> str:
    """Обрезать текст до максимальной длины."""
    if len(text) > max_length:
        return text[:max_length-3] + "..."
    return text


def format_order_header(first_name: str, last_name: str, username: str) -> str:
    """Форматировать заголовок нового заказа."""
    date_str, time_str = format_date_time()

    header = f"✨ {get_text('new_order_header')}\n\n"
    header += f"🕒 {date_str} • {time_str}\n"
    header += f"🆔 {get_text('new_order_id')} {{}}\n"

    name_part = f"👤 {first_name}"
    if last_name:
        name_part += f" {last_name}"

    header += name_part + "\n"

    if username:
        header += f"🔗 @{username}\n"

    header += f"\n{get_text('separator')}\n\n"
    return header


def format_reply_header(first_name: str, last_name: str, username: str) -> str:
    """Форматировать заголовок ответа."""
    date_str, time_str = format_date_time()

    header = f"📩 {get_text('reply_header')}\n"
    header += f"{first_name}"

    if last_name:
        header += f" {last_name}"

    if username:
        header += f" (@{username})"

    header += f"\n\n🕒 {date_str} • {time_str}\n\n"
    header += f"{get_text('reply_separator')}\n\n"
    return header


def parse_user_id_from_text(text: str) -> int:
    """Парсить User ID из текста (ожидается только цифры)."""
    try:
        return int(text.strip())
    except ValueError:
        return None
