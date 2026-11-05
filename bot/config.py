"""Настройки из переменных окружения и необязательного файла .env."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


def load_dotenv(path: Path, env: dict[str, str] | None = None) -> None:
    """Для запуска без Docker. Уже заданные переменные не перезаписывает."""
    target = os.environ if env is None else env
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        target.setdefault(key.strip(), value.strip().strip("'\""))


def _parse_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ConfigError(f"OWNER_IDS: «{part}» не похоже на Telegram ID")
        ids.add(int(part))
    return frozenset(ids)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    owner_ids: frozenset[int]
    database_url: str
    secret_key: str | None = None
    log_level: str = "INFO"
    db_pool_size: int = 5
    max_concurrent_updates: int = 256
    polling_timeout: int = 30
    stats_flush_seconds: float = 5.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        missing = [k for k in ("BOT_TOKEN", "OWNER_IDS", "DATABASE_URL") if not source.get(k)]
        if missing:
            raise ConfigError("Не заданы: " + ", ".join(missing))
        owners = _parse_ids(source["OWNER_IDS"])
        if not owners:
            raise ConfigError("OWNER_IDS: нужен хотя бы один владелец")
        try:
            return cls(
                bot_token=source["BOT_TOKEN"].strip(),
                owner_ids=owners,
                database_url=source["DATABASE_URL"].strip(),
                secret_key=(source.get("SECRET_KEY") or "").strip() or None,
                log_level=(source.get("LOG_LEVEL") or "INFO").strip().upper(),
                db_pool_size=int(source.get("DB_POOL_SIZE") or 5),
                max_concurrent_updates=int(source.get("MAX_CONCURRENT_UPDATES") or 256),
                polling_timeout=int(source.get("POLLING_TIMEOUT") or 30),
                stats_flush_seconds=float(source.get("STATS_FLUSH_SECONDS") or 5),
            )
        except ValueError as exc:
            raise ConfigError(f"Неверное числовое значение: {exc}") from exc
