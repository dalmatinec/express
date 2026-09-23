"""
Тексты бота.
Дефолтные значения лежат в texts.json, изменённые через /settext — в БД.
Тексты в HTML-разметке Telegram.
"""

import json
from pathlib import Path

from database import db

DISABLED = "-"  # значение текста, при котором сообщение не отправляется

with open(Path(__file__).with_name("texts.json"), encoding="utf-8") as f:
    DEFAULTS: dict = json.load(f)


def get_raw(key: str) -> str:
    """Текущий шаблон текста (из БД, иначе из texts.json)."""
    custom = db.get_text(key)
    if custom is not None:
        return custom
    return DEFAULTS[key]["text"]


def render(key: str, **params) -> str:
    """
    Получить текст с подставленными значениями.
    Возвращает пустую строку, если текст отключён («-»).
    Подстановка через replace, чтобы случайные фигурные скобки в тексте не ломали бота.
    """
    template = get_raw(key)
    if template.strip() == DISABLED:
        return ""
    for name, value in params.items():
        template = template.replace("{" + name + "}", str(value))
    return template
