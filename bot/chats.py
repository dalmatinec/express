"""Чаты бота и их настройки. На частом пути читаем из кэша и не трогаем базу."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cache import TTLCache
from .db import queries as q


@dataclass(frozen=True, slots=True)
class ChatInfo:
    type: str
    title: str
    username: str | None
    is_active: bool
    settings: dict[str, Any]


class ChatRepo:
    def __init__(self, db: Any, *, cache_size: int = 20_000, ttl: float = 600.0) -> None:
        self._db = db
        self.cache: TTLCache[tuple[int, int], ChatInfo | None] = TTLCache(cache_size, ttl)

    async def get(self, bot_id: int, chat_id: int) -> ChatInfo | None:
        return await self.cache.get_or_load(
            (bot_id, chat_id), lambda: self._load(bot_id, chat_id)
        )

    async def _load(self, bot_id: int, chat_id: int) -> ChatInfo | None:
        row = await self._db.fetchrow(q.GET_CHAT, bot_id, chat_id)
        if row is None:
            return None
        return ChatInfo(
            type=row["type"],
            title=row["title"],
            username=row["username"],
            is_active=row["is_active"],
            settings=row["settings"],
        )

    async def touch(
        self, bot_id: int, chat_id: int, chat_type: str, title: str | None, username: str | None
    ) -> ChatInfo:
        """Регистрирует чат при первом событии и обновляет его только при изменениях."""
        title = title or ""
        info = await self.get(bot_id, chat_id)
        if (
            info is not None
            and info.is_active
            and info.title == title
            and info.username == username
            and info.type == chat_type
        ):
            return info
        await self._db.execute(q.UPSERT_CHAT, bot_id, chat_id, chat_type, title, username)
        self.cache.delete((bot_id, chat_id))
        fresh = await self.get(bot_id, chat_id)
        assert fresh is not None
        return fresh

    async def set_active(self, bot_id: int, chat_id: int, active: bool) -> None:
        await self._db.execute(q.SET_CHAT_ACTIVE, bot_id, chat_id, active)
        self.cache.delete((bot_id, chat_id))

    async def update_settings(self, bot_id: int, chat_id: int, changes: dict[str, Any]) -> None:
        await self._db.execute(q.MERGE_CHAT_SETTINGS, bot_id, chat_id, changes)
        self.cache.delete((bot_id, chat_id))

    async def migrate(self, bot_id: int, old_id: int, new_id: int) -> None:
        async with self._db.transaction() as connection:
            await connection.execute(q.MIGRATE_CHAT, bot_id, old_id, new_id)
            await connection.execute(q.MIGRATE_MEMBERS, bot_id, old_id, new_id)
        self.cache.delete((bot_id, old_id))
        self.cache.delete((bot_id, new_id))

    async def count_active(self, bot_id: int) -> int:
        return await self._db.fetchval(q.COUNT_ACTIVE_CHATS, bot_id)
