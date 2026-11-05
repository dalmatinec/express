"""Точка входа: python -m bot"""
from __future__ import annotations

import asyncio
import gc
import logging
import signal
import sys
from contextlib import suppress
from pathlib import Path

from .config import ConfigError, Settings, load_dotenv

log = logging.getLogger("bot")


async def run(settings: Settings) -> int:
    # Импорт здесь, чтобы ошибка настроек показывалась сразу и понятно.
    from aiogram.exceptions import TelegramUnauthorizedError

    from .app import build_app, build_dispatcher
    from .db.migrate import migrate
    from .db.pool import Database
    from .runtime import BotManager

    db = await Database.connect(settings.database_url, max_size=settings.db_pool_size)
    exit_code = 0
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    stats_task = None
    manager = None
    try:
        applied = await migrate(db)
        if applied:
            log.info("Применены миграции: %s", ", ".join(applied))
        app = build_app(settings, db)
        restored = await app.fsm.load()
        if restored:
            log.info("Восстановлено незаконченных диалогов: %d", restored)
        dp = build_dispatcher(app)

        def on_main_fatal() -> None:
            nonlocal exit_code
            exit_code = 1
            stop.set()

        manager = BotManager(
            dp,
            db,
            owner_id=min(settings.owner_ids),
            secret_key=settings.secret_key,
            polling_timeout=settings.polling_timeout,
            max_concurrent_updates=settings.max_concurrent_updates,
            on_main_fatal=on_main_fatal,
        )
        app.manager = manager
        stats_task = asyncio.create_task(app.stats.run(), name="stats")
        try:
            await manager.start(settings.bot_token)
        except TelegramUnauthorizedError:
            log.critical("Telegram не принял BOT_TOKEN. Проверьте токен у @BotFather.")
            return 1

        # Всё загруженное при старте больше не обходит сборщик мусора.
        gc.collect()
        gc.freeze()
        await stop.wait()
        log.info("Останавливаюсь")
        return exit_code
    finally:
        if manager is not None:
            await manager.stop()
        if stats_task is not None:
            stats_task.cancel()
            with suppress(asyncio.CancelledError):
                await stats_task
            with suppress(Exception):
                if app_stats_flushed := await app.stats.flush():
                    log.info("Дописано в базу при остановке: %d", app_stats_flushed)
        await db.close()


def main() -> int:
    load_dotenv(Path(".env"))
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Ошибка настроек: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # aiogram пишет строку на каждое событие: в большом чате это лишняя работа и шум.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    # На боте много короткоживущих объектов: реже запускаем сборщик мусора.
    gc.set_threshold(10_000, 20, 20)

    with suppress(ImportError):
        import uvloop

        uvloop.install()

    return asyncio.run(run(settings))


if __name__ == "__main__":
    raise SystemExit(main())
