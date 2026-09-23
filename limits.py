"""
Настройки антифлуда. Значения по умолчанию — из config.py/.env,
изменённые в админ-панели хранятся в БД (таблица settings).
"""

from dataclasses import dataclass

from config import (
    FLOOD_MAX_MESSAGES, FLOOD_WINDOW, FLOOD_MUTE_SECONDS,
    FLOOD_MIN_INTERVAL, DUPLICATE_WINDOW, MAX_TEXT_LENGTH,
)
from database import db


@dataclass(frozen=True)
class Limit:
    title: str
    hint: str
    default: int
    minimum: int
    maximum: int
    unit: str


LIMITS: dict[str, Limit] = {
    "flood_max": Limit("Сообщений подряд", "Сколько сообщений можно отправить за период (ниже)",
                       FLOOD_MAX_MESSAGES, 1, 1000, "шт."),
    "flood_window": Limit("За период", "За сколько секунд считаются сообщения",
                          FLOOD_WINDOW, 1, 3600, "сек"),
    "flood_mute": Limit("Мут", "На сколько секунд клиент не может писать после превышения",
                        FLOOD_MUTE_SECONDS, 1, 86400, "сек"),
    "flood_interval": Limit("Пауза между сообщениями", "Минимум секунд между двумя сообщениями, 0 — выключено",
                            FLOOD_MIN_INTERVAL, 0, 3600, "сек"),
    "duplicate_window": Limit("Повтор текста", "Один и тот же текст не принимается столько секунд, 0 — выключено",
                              DUPLICATE_WINDOW, 0, 86400, "сек"),
    "max_length": Limit("Длина сообщения", "Максимум символов в одном сообщении",
                        MAX_TEXT_LENGTH, 1, 4000, "симв."),
}


def get_limit(key: str) -> int:
    value = db.get_setting(f"limit:{key}")
    if value is not None:
        try:
            return int(value)
        except ValueError:
            pass
    return LIMITS[key].default


def set_limit(key: str, value: int):
    db.set_setting(f"limit:{key}", str(value))


def reset_limits():
    for key in LIMITS:
        db.set_setting(f"limit:{key}", None)


def format_seconds(seconds: float) -> str:
    """125 -> «2 мин 5 сек»."""
    seconds = max(1, int(seconds + 0.999))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if sec or not parts:
        parts.append(f"{sec} сек")
    return " ".join(parts)
