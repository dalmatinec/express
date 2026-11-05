"""Тонкая обёртка над пулом asyncpg, без ORM: меньше памяти и накладных расходов."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


async def _init_connection(connection: Any) -> None:
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


class Database:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str, *, min_size: int = 1, max_size: int = 5) -> Database:
        import asyncpg

        pool = await asyncpg.create_pool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
            # Простаивающие соединения закрываются и не держат память Postgres.
            max_inactive_connection_lifetime=120,
            init=_init_connection,
        )
        return cls(pool)

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return await self._pool.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return await self._pool.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await self._pool.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        return await self._pool.execute(sql, *args)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                yield connection

    async def close(self) -> None:
        await self._pool.close()
