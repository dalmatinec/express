"""Правила антиспама. Чистые функции без обращений к сети и базе."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from .settings import ProtectionSettings

TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me", "telegram.dog", "telesco.pe"})

# Латиница, похожая на кириллицу: спамеры пишут «кaзинo» с такими буквами.
_LOOKALIKE = str.maketrans("aceopxyikmtbh", "асеорхуикмтвн")


@dataclass(frozen=True, slots=True)
class MessageView:
    text: str = ""
    links: tuple[str, ...] = ()
    mentions: int = 0
    is_channel_forward: bool = False


def _split(url: str) -> tuple[str, str, str]:
    """-> (схема, домен, путь) в нижнем регистре."""
    candidate = url.strip()
    if "://" not in candidate and not candidate.lower().startswith("tg:"):
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "", "", ""
    return parts.scheme.lower(), host.removeprefix("www."), parts.path.lower().rstrip("/")


def is_telegram_link(url: str) -> bool:
    scheme, host, _ = _split(url)
    return scheme == "tg" or host in TELEGRAM_HOSTS or host.endswith(".t.me")


def is_allowed(url: str, allow: tuple[str, ...], own_username: str | None) -> bool:
    _, host, path = _split(url)
    if own_username and host in TELEGRAM_HOSTS:
        name = path.lstrip("/").split("/", 1)[0]
        if name == own_username.lower():
            return True
    target = f"{host}{path}"
    for item in allow:
        rule = item.lower().strip().removeprefix("https://").removeprefix("www.").rstrip("/")
        if not rule:
            continue
        if "/" in rule:
            if target == rule or target.startswith(rule + "/"):
                return True
        elif host == rule or host.endswith("." + rule):
            return True
    return False


def normalize(text: str) -> str:
    return text.casefold().translate(_LOOKALIKE)


@lru_cache(maxsize=256)
def _stopword_pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    # Слова экранируются: регулярное выражение от админа не сможет подвесить бота.
    parts = [re.escape(normalize(word.strip())) for word in words if word.strip()]
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + ")")


def find_violation(
    view: MessageView, settings: ProtectionSettings, own_username: str | None = None
) -> str | None:
    """Код нарушения или None. Сначала дешёвые проверки."""
    if settings.block_forwards and view.is_channel_forward:
        return "forward"
    if settings.mention_limit and view.mentions >= settings.mention_limit:
        return "mentions"
    if settings.link_mode != "off":
        for url in view.links:
            if is_allowed(url, settings.link_allow, own_username):
                continue
            if settings.link_mode == "all" or is_telegram_link(url):
                return "links"
    if settings.stopwords and view.text:
        pattern = _stopword_pattern(settings.stopwords)
        if pattern is not None and pattern.search(normalize(view.text)):
            return "stopword"
    return None
