"""Копит счётчики сообщений и данные людей в памяти и пишет в базу пачками.

Вместо одной записи в базу на каждое сообщение идёт одна транзакция раз в несколько
секунд, и сообщения одного человека в одном чате сворачиваются в одну строку.
При аварийном падении теряются только последние секунды счётчиков.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from .db import queries as q

log = logging.getLogger(__name__)

MemberKey = tuple[int, int, int]


class StatsBuffer:
    def __init__(
        self,
        db: Any,
        *,
        flush_seconds: float = 5.0,
        flush_threshold: int = 5_000,
        max_pending: int = 200_000,
        known_users: int = 50_000,
    ) -> None:
        self._db = db
        self._flush_seconds = flush_seconds
        self._flush_threshold = flush_threshold
        self._max_pending = max_pending
        self._known_limit = known_users
        self._members: dict[MemberKey, list[Any]] = {}
        self._users: dict[int, tuple[str | None, str]] = {}
        # Хранится хэш имени, а не сами строки: так 50 тысяч людей занимают мало памяти.
        self._known: OrderedDict[int, int] = OrderedDict()
        self._lock = asyncio.Lock()
        self._wakeup = asyncio.Event()
        self.flushed_rows = 0
        self.dropped_rows = 0

    @property
    def pending(self) -> int:
        return len(self._members) + len(self._users)

    def add_message(
        self, bot_id: int, chat_id: int, user_id: int, when: datetime | None = None
    ) -> None:
        when = when or datetime.now(UTC)
        key = (bot_id, chat_id, user_id)
        row = self._members.get(key)
        if row is None:
            self._members[key] = [1, when]
        else:
            row[0] += 1
            if when > row[1]:
                row[1] = when
        if self.pending >= self._flush_threshold:
            self._wakeup.set()

    def touch_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        name = first_name or ""
        fingerprint = hash((username, name))
        if self._known.get(user_id) == fingerprint:
            self._known.move_to_end(user_id)
            return
        self._known[user_id] = fingerprint
        self._known.move_to_end(user_id)
        while len(self._known) > self._known_limit:
            self._known.popitem(last=False)
        self._users[user_id] = (username, name)
        if self.pending >= self._flush_threshold:
            self._wakeup.set()

    async def flush(self) -> int:
        """Возвращает число записанных строк. При ошибке данные остаются до следующего раза."""
        async with self._lock:
            if not self._members and not self._users:
                return 0
            members, self._members = self._members, {}
            users, self._users = self._users, {}
            try:
                await self._write(members, users)
            except Exception:
                self._restore(members, users)
                raise
            written = len(members) + len(users)
            self.flushed_rows += written
            return written

    async def _write(
        self, members: dict[MemberKey, list[Any]], users: dict[int, tuple[str | None, str]]
    ) -> None:
        async with self._db.transaction() as connection:
            # Счётчики не критичны: не ждём fsync, зато пишем заметно быстрее.
            await connection.execute("SET LOCAL synchronous_commit = off")
            if users:
                # Сортировка не даёт двум транзакциям заблокировать друг друга.
                ids = sorted(users)
                await connection.execute(
                    q.UPSERT_USERS, ids, [users[i][0] for i in ids], [users[i][1] for i in ids]
                )
            if members:
                keys = sorted(members)
                await connection.execute(
                    q.UPSERT_MEMBERS,
                    [k[0] for k in keys],
                    [k[1] for k in keys],
                    [k[2] for k in keys],
                    [members[k][0] for k in keys],
                    [members[k][1] for k in keys],
                )

    def _restore(
        self, members: dict[MemberKey, list[Any]], users: dict[int, tuple[str | None, str]]
    ) -> None:
        for key, (count, when) in members.items():
            row = self._members.get(key)
            if row is not None:
                row[0] += count
                if when > row[1]:
                    row[1] = when
            elif len(self._members) < self._max_pending:
                self._members[key] = [count, when]
            else:
                self.dropped_rows += 1
        for user_id, value in users.items():
            if user_id in self._users:
                continue  # новые данные свежее
            if len(self._users) < self._max_pending:
                self._users[user_id] = value
            else:
                self.dropped_rows += 1

    async def run(self) -> None:
        """Фоновая запись. Останавливается отменой задачи; итоговый flush делает вызывающий."""
        while True:
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._flush_seconds)
            except TimeoutError:
                pass
            self._wakeup.clear()
            try:
                await self.flush()
            except Exception:
                log.exception("Не удалось записать статистику, повторю позже")
                # База недоступна: не молотим повторами, если буфер переполнен.
                await asyncio.sleep(self._flush_seconds)
