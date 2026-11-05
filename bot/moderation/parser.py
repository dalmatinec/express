"""Разбор команд: /mute @vasya 2ч спам, !бан 12345 навсегда реклама, /мут 2 часа и т.п."""
from __future__ import annotations

import re
from dataclasses import dataclass

MIN_SECONDS = 30  # Telegram считает более короткий срок вечным
MAX_SECONDS = 366 * 86_400  # и более длинный тоже
REASON_LIMIT = 200

_UNITS = {
    60 * 60 * 24 * 365: ("y", "г", "год", "года", "лет"),
    60 * 60 * 24 * 30: ("mo", "мес", "месяц", "месяца", "месяцев"),
    60 * 60 * 24 * 7: ("w", "н", "нед", "неделя", "недели", "неделю", "недель"),
    60 * 60 * 24: ("d", "д", "дн", "день", "дня", "дней"),
    60 * 60: ("h", "ч", "час", "часа", "часов"),
    60: ("m", "м", "мин", "минута", "минуты", "минуту", "минут"),
    1: ("s", "с", "сек", "секунда", "секунды", "секунду", "секунд"),
}
UNIT_SECONDS = {name: seconds for seconds, names in _UNITS.items() for name in names}

_PART = re.compile(r"(\d+)([a-zа-яё]+)", re.IGNORECASE)
_FOREVER = frozenset({"навсегда", "forever", "perm", "вечно"})
_USERNAME = re.compile(r"@[A-Za-z0-9_]{4,32}")


def parse_duration(token: str) -> int | None:
    """2ч, 1д12ч, 30мин -> секунды. Не срок -> None."""
    token = token.lower()
    position = total = 0
    for match in _PART.finditer(token):
        if match.start() != position:
            return None
        seconds = UNIT_SECONDS.get(match.group(2))
        if seconds is None:
            return None
        total += int(match.group(1)) * seconds
        position = match.end()
    return total if position == len(token) and total > 0 else None


@dataclass(frozen=True, slots=True)
class ParsedArgs:
    target: str | None = None  # @username или числовой ID
    seconds: int | None = None  # None: навсегда
    reason: str | None = None
    error: str | None = None


def _is_target_token(token: str) -> bool:
    # Короткое число это не ID, а скорее срок вроде «5 минут» или часть причины.
    return bool(_USERNAME.fullmatch(token)) or (token.isdigit() and len(token) >= 5)


def parse_args(args: str | None, *, with_duration: bool, has_target: bool = False) -> ParsedArgs:
    """has_target: цель уже известна из упоминания без username, искать её в тексте не нужно."""
    tokens = (args or "").split()
    target: str | None = None
    if tokens and not has_target and _is_target_token(tokens[0]):
        target = tokens.pop(0)

    seconds: int | None = None
    if with_duration and tokens:
        head = tokens[0].lower()
        if head in _FOREVER:
            tokens.pop(0)
        else:
            parsed = parse_duration(head)
            used = 1
            if parsed is None and head.isdigit() and len(tokens) > 1:
                unit = UNIT_SECONDS.get(tokens[1].lower())
                if unit is not None:
                    parsed, used = int(head) * unit, 2
            if parsed is not None:
                del tokens[:used]
                seconds = parsed
                if seconds < MIN_SECONDS:
                    return ParsedArgs(error="too_short")
                if seconds > MAX_SECONDS:
                    return ParsedArgs(error="too_long")

    reason = " ".join(tokens)[:REASON_LIMIT] or None
    return ParsedArgs(target=target, seconds=seconds, reason=reason)


def format_duration(seconds: int) -> str:
    """Две старшие единицы, этого достаточно для маленькой карточки."""
    parts: list[str] = []
    for size, label in ((86_400, "д"), (3_600, "ч"), (60, "мин"), (1, "сек")):
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount} {label}")
        if len(parts) == 2:
            break
    return " ".join(parts) or "0 сек"
