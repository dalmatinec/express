"""Подключение FsmStore к aiogram."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from .fsm_store import FsmKey, FsmStore


def _key(key: StorageKey) -> FsmKey:
    return (key.bot_id, key.chat_id, key.user_id, key.thread_id or 0, key.destiny)


class PgStorage(BaseStorage):
    def __init__(self, store: FsmStore) -> None:
        self._store = store

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await self._store.set_state(_key(key), value)

    async def get_state(self, key: StorageKey) -> str | None:
        return self._store.get_state(_key(key))

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._store.set_data(_key(key), dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        return self._store.get_data(_key(key))

    async def close(self) -> None:
        return None
