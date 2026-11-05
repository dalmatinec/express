"""Журнал наказаний в базе."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..db import queries as q

SINGLE_ACTIVE = frozenset({"ban", "mute"})


class PunishmentRepo:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def record(
        self,
        bot_id: int,
        chat_id: int,
        user_id: int,
        kind: str,
        admin_id: int,
        reason: str | None,
        expires_at: datetime | None,
        source: str = "manual",
    ) -> int:
        return await self._db.fetchval(
            q.INSERT_PUNISHMENT,
            bot_id, chat_id, user_id, kind, admin_id, reason, expires_at, source,
        )

    async def revoke_active(
        self, bot_id: int, chat_id: int, user_id: int, kind: str, by: int
    ) -> bool:
        rows = await self._db.fetch(q.REVOKE_ACTIVE, bot_id, chat_id, user_id, kind, by)
        return bool(rows)

    async def count_warns(self, bot_id: int, chat_id: int, user_id: int) -> int:
        return await self._db.fetchval(q.COUNT_ACTIVE_WARNS, bot_id, chat_id, user_id)

    async def revoke_last_warn(
        self, bot_id: int, chat_id: int, user_id: int, by: int
    ) -> int | None:
        return await self._db.fetchval(q.REVOKE_LAST_WARN, bot_id, chat_id, user_id, by)

    async def get(self, punishment_id: int, bot_id: int, chat_id: int) -> Any:
        return await self._db.fetchrow(q.GET_PUNISHMENT, punishment_id, bot_id, chat_id)

    async def revoke_by_id(self, punishment_id: int, by: int) -> bool:
        return await self._db.fetchval(q.REVOKE_BY_ID, punishment_id, by) is not None

    async def history(self, bot_id: int, chat_id: int, user_id: int, limit: int) -> list[Any]:
        return await self._db.fetch(q.USER_HISTORY, bot_id, chat_id, user_id, limit)

    async def journal(self, bot_id: int, chat_id: int, limit: int) -> list[Any]:
        return await self._db.fetch(q.CHAT_JOURNAL, bot_id, chat_id, limit)
