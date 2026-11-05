"""Состояния диалогов: чтение из памяти, запись и в память, и в базу.

aiogram запрашивает состояние на каждое событие, то есть на каждое сообщение в каждом чате.
Если ходить за ним в базу, модератор получит лишний запрос на сообщение. Здесь чтение
никогда не трогает базу, а база нужна только чтобы диалог пережил перезапуск.
"""
from __future__ import annotations

from typing import Any

from .db import queries as q

# (bot_id, chat_id, user_id, thread_id, destiny)
FsmKey = tuple[int, int, int, int, str]

STALE_DAYS = 3


class FsmStore:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._items: dict[FsmKey, tuple[str | None, dict[str, Any]]] = {}

    def __len__(self) -> int:
        return len(self._items)

    async def load(self) -> int:
        await self._db.execute(q.FSM_PURGE, STALE_DAYS)
        rows = await self._db.fetch(q.FSM_LOAD, STALE_DAYS)
        self._items = {
            (r["bot_id"], r["chat_id"], r["user_id"], r["thread_id"], r["destiny"]): (
                r["state"],
                r["data"],
            )
            for r in rows
        }
        return len(self._items)

    def get_state(self, key: FsmKey) -> str | None:
        item = self._items.get(key)
        return item[0] if item else None

    def get_data(self, key: FsmKey) -> dict[str, Any]:
        item = self._items.get(key)
        return dict(item[1]) if item else {}

    async def set_state(self, key: FsmKey, state: str | None) -> None:
        await self._save(key, state, self.get_data(key))

    async def set_data(self, key: FsmKey, data: dict[str, Any]) -> None:
        await self._save(key, self.get_state(key), dict(data))

    async def _save(self, key: FsmKey, state: str | None, data: dict[str, Any]) -> None:
        if state is None and not data:
            if key in self._items:
                await self._db.execute(q.FSM_DELETE, *key)
                self._items.pop(key, None)
            return
        await self._db.execute(q.FSM_UPSERT, *key, state, data)
        self._items[key] = (state, data)
