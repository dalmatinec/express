"""Подставная база: запоминает запросы, настоящий Postgres проверяется в test_sql.py."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any


class FakeConnection:
    def __init__(self, db: FakeDb) -> None:
        self._db = db

    async def execute(self, sql: str, *args: Any) -> str:
        return await self._db.execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return await self._db.fetch(sql, *args)

    @asynccontextmanager
    async def transaction(self) -> Any:
        yield self


class FakeDb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_next = 0
        self.rows: list[Any] = []

    def _record(self, sql: str, args: tuple[Any, ...]) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("база недоступна")
        self.calls.append((sql, args))

    async def execute(self, sql: str, *args: Any) -> str:
        self._record(sql, args)
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self._record(sql, args)
        return self.rows

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self._record(sql, args)
        return self.rows[0] if self.rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._record(sql, args)
        return None

    @asynccontextmanager
    async def transaction(self) -> Any:
        yield FakeConnection(self)

    def calls_of(self, sql: str) -> list[tuple[Any, ...]]:
        return [args for query, args in self.calls if query == sql]
