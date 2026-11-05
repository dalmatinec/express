"""Настройки защиты чата. Значения из базы приводятся к допустимым границам."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

LINK_MODES = ("telegram", "all", "off")
SPAM_ACTIONS = ("warn", "delete")
FLOOD_ACTIONS = ("mute", "kick", "ban")


def _int(data: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _choice(data: dict[str, Any], key: str, default: str, allowed: tuple[str, ...]) -> str:
    value = data.get(key, default)
    return value if value in allowed else default


def _flag(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    return value if isinstance(value, bool) else default


def _words(data: dict[str, Any], key: str, limit: int) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(str(item)[:64] for item in value[:limit] if str(item).strip())


@dataclass(frozen=True, slots=True)
class ProtectionSettings:
    flood_enabled: bool = True
    flood_limit: int = 7
    flood_window: int = 5
    flood_action: str = "mute"
    flood_seconds: int = 600
    link_mode: str = "telegram"
    link_allow: tuple[str, ...] = ()
    block_forwards: bool = False
    mention_limit: int = 6  # 0: выключено
    duplicate_limit: int = 4  # 0: выключено
    duplicate_window: int = 60
    stopwords: tuple[str, ...] = ()
    spam_action: str = "warn"
    notice_seconds: int = 20

    @classmethod
    def from_chat(cls, settings: dict[str, Any] | None) -> ProtectionSettings:
        raw = (settings or {}).get("protect")
        data = raw if isinstance(raw, dict) else {}
        return cls(
            flood_enabled=_flag(data, "flood_enabled", True),
            flood_limit=_int(data, "flood_limit", 7, 2, 50),
            flood_window=_int(data, "flood_window", 5, 1, 120),
            flood_action=_choice(data, "flood_action", "mute", FLOOD_ACTIONS),
            flood_seconds=_int(data, "flood_seconds", 600, 30, 366 * 86_400),
            link_mode=_choice(data, "link_mode", "telegram", LINK_MODES),
            link_allow=_words(data, "link_allow", 100),
            block_forwards=_flag(data, "block_forwards", False),
            mention_limit=_int(data, "mention_limit", 6, 0, 100),
            duplicate_limit=_int(data, "duplicate_limit", 4, 0, 50),
            duplicate_window=_int(data, "duplicate_window", 60, 5, 3600),
            stopwords=_words(data, "stopwords", 300),
            spam_action=_choice(data, "spam_action", "warn", SPAM_ACTIONS),
            notice_seconds=_int(data, "notice_seconds", 20, 3, 600),
        )

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["link_allow"] = list(self.link_allow)
        data["stopwords"] = list(self.stopwords)
        return data


def _next(value: str, options: tuple[str, ...]) -> str:
    return options[(options.index(value) + 1) % len(options)]


# Кнопки панели: только эти поля можно менять нажатием.
def toggle(settings: ProtectionSettings, field: str) -> ProtectionSettings:
    if field == "flood":
        return replace(settings, flood_enabled=not settings.flood_enabled)
    if field == "links":
        return replace(settings, link_mode=_next(settings.link_mode, LINK_MODES))
    if field == "forwards":
        return replace(settings, block_forwards=not settings.block_forwards)
    if field == "mentions":
        return replace(settings, mention_limit=0 if settings.mention_limit else 6)
    if field == "duplicates":
        return replace(settings, duplicate_limit=0 if settings.duplicate_limit else 4)
    if field == "action":
        return replace(settings, spam_action=_next(settings.spam_action, SPAM_ACTIONS))
    raise ValueError(f"Неизвестная настройка: {field}")
