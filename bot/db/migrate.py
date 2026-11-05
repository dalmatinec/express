"""Применяет новые SQL-файлы из migrations/ по порядку, каждый в своей транзакции."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Защита от двух одновременных запусков: произвольный постоянный номер блокировки.
ADVISORY_LOCK_ID = 7_415_902

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("*.sql"))


async def migrate(db: Any, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Возвращает имена применённых миграций."""
    applied: list[str] = []
    async with db.transaction() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock($1::bigint)", ADVISORY_LOCK_ID)
        await connection.execute(CREATE_TABLE)
        rows = await connection.fetch("SELECT version FROM schema_migrations")
        done = {row["version"] for row in rows}
        for path in migration_files(directory):
            if path.name in done:
                continue
            log.info("Применяю миграцию %s", path.name)
            async with connection.transaction():
                await connection.execute(path.read_text(encoding="utf-8"))
                await connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1::text)", path.name
                )
            applied.append(path.name)
    return applied
