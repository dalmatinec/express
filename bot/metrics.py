"""Сколько памяти занимает процесс и что считать сообщением участника."""
from __future__ import annotations

from pathlib import Path

# Служебные события (вступил, вышел, закрепил и т.п.) в статистику сообщений не идут.
USER_CONTENT = frozenset({
    "text", "animation", "audio", "document", "photo", "sticker", "story", "video",
    "video_note", "voice", "contact", "dice", "game", "poll", "venue", "location",
    "paid_media", "live_photo", "checklist",
})


def is_countable(content_type: str, *, is_bot: bool, has_sender_chat: bool) -> bool:
    # Ботов, анонимных админов и посты от имени канала не считаем: это не живой участник.
    return not is_bot and not has_sender_chat and content_type in USER_CONTENT


def rss_mb(status_path: str = "/proc/self/status") -> float | None:
    try:
        for line in Path(status_path).read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None
