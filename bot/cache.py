"""Кэш в памяти процесса: ограничен по размеру и времени жизни записи.

Бот работает одним процессом, поэтому отдельный Redis для кэша не нужен: запись идёт
через этот же процесс и сразу сбрасывает нужный ключ.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable

_MISSING = object()


class TTLCache[K: Hashable, V]:
    def __init__(
        self,
        maxsize: int,
        ttl: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self.hits = 0
        self.misses = 0
        self._clock = clock
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._inflight: dict[K, asyncio.Task[V]] = {}

    def __len__(self) -> int:
        return len(self._data)

    def _lookup(self, key: K) -> object:
        item = self._data.get(key)
        if item is None:
            return _MISSING
        expires, value = item
        if expires <= self._clock():
            del self._data[key]
            return _MISSING
        self._data.move_to_end(key)
        return value

    def get(self, key: K, default: V | None = None) -> V | None:
        value = self._lookup(key)
        if value is _MISSING:
            self.misses += 1
            return default
        self.hits += 1
        return value  # type: ignore[return-value]

    def set(self, key: K, value: V) -> None:
        self._data[key] = (self._clock() + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def delete(self, key: K) -> None:
        self._data.pop(key, None)
        # Идущая загрузка могла прочитать данные до записи: её результат в кэш не попадёт.
        self._inflight.pop(key, None)

    def clear(self) -> None:
        self._data.clear()
        self._inflight.clear()

    async def get_or_load(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        """Одновременные промахи по одному ключу дают только одну загрузку."""
        value = self._lookup(key)
        if value is not _MISSING:
            self.hits += 1
            return value  # type: ignore[return-value]
        self.misses += 1
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._load(key, loader))
            self._inflight[key] = task
        # shield: отмена одного ожидающего не должна срывать загрузку для остальных.
        return await asyncio.shield(task)

    async def _load(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        task = asyncio.current_task()
        try:
            value = await loader()
            if self._inflight.get(key) is task:
                self.set(key, value)
            return value
        finally:
            if self._inflight.get(key) is task:
                del self._inflight[key]
