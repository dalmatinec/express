"""Счётчики флуда и повторов в памяти. Размер ограничен, база не участвует."""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

Key = tuple[int, int, int]  # бот, чат, человек

# Короткие «да», «ок» повторяют и обычные люди: это не спам.
MIN_DUPLICATE_LENGTH = 10


@dataclass(frozen=True, slots=True)
class Hit:
    flood: bool = False
    duplicate: bool = False
    cooldown: bool = False
    message_ids: tuple[int, ...] = ()


class _Entry:
    __slots__ = ("album", "cooldown_until", "dup_count", "dup_hash", "dup_time", "times")

    def __init__(self, limit: int) -> None:
        self.times: deque[tuple[float, int]] = deque(maxlen=limit)
        self.album: str | None = None
        self.dup_hash = 0
        self.dup_count = 0
        self.dup_time = 0.0
        self.cooldown_until = 0.0


class Tracker:
    def __init__(self, maxsize: int = 20_000, clock: Callable[[], float] = time.monotonic) -> None:
        self._maxsize = maxsize
        self._clock = clock
        self._entries: OrderedDict[Key, _Entry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def hit(
        self,
        key: Key,
        message_id: int,
        *,
        text: str = "",
        album: str | None = None,
        flood_limit: int = 0,
        flood_window: int = 0,
        duplicate_limit: int = 0,
        duplicate_window: int = 0,
    ) -> Hit:
        now = self._clock()
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry(max(flood_limit, 1))
            self._entries[key] = entry
            if len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
        else:
            self._entries.move_to_end(key)

        if now < entry.cooldown_until:
            return Hit(cooldown=True, message_ids=(message_id,))

        # Альбом приходит несколькими сообщениями, но написан один раз.
        same_album = album is not None and album == entry.album
        entry.album = album

        if flood_limit and not same_album:
            if entry.times.maxlen != flood_limit:
                entry.times = deque(entry.times, maxlen=flood_limit)
            entry.times.append((now, message_id))
            if len(entry.times) == flood_limit and now - entry.times[0][0] <= flood_window:
                ids = tuple(item[1] for item in entry.times)
                entry.times.clear()
                entry.cooldown_until = now + flood_window
                return Hit(flood=True, message_ids=ids)

        if duplicate_limit and len(text) >= MIN_DUPLICATE_LENGTH:
            digest = hash(text)
            if digest == entry.dup_hash and now - entry.dup_time <= duplicate_window:
                entry.dup_count += 1
            else:
                entry.dup_hash, entry.dup_count = digest, 1
            entry.dup_time = now
            if entry.dup_count >= duplicate_limit:
                entry.dup_count = 0
                return Hit(duplicate=True, message_ids=(message_id,))
        return Hit()
