"""Фоновые задачи: отложенные посты, автоудаление, бэкап раз в сутки."""
import asyncio
import logging

from ..app import App, now
from .backup import make_backup, send_backup
from .publisher import run_due

log = logging.getLogger(__name__)

TICK = 15  # секунд между проверками


async def daily_backup(app: App) -> None:
    hour = int(app.setting("backup_hour", 4))
    local = app.local(now())
    today = local.strftime("%Y-%m-%d")
    if local.hour < hour or app.setting("backup_day") == today:
        return
    await app.set_setting("backup_day", today)
    # заодно чистим брошенные черновики старше двух недель
    await app.db.execute("DELETE FROM posts WHERE is_draft = 1 AND updated_at < ?", (now() - 14 * 86400,))
    await app.media.cleanup()
    path = await make_backup(app)
    errors = await send_backup(app, path, "Автобэкап раз в сутки.")
    for e in errors:
        log.warning("Бэкап не ушёл: %s", e)


async def run_jobs(app: App) -> None:
    while True:
        try:
            await run_due(app)
        except Exception:
            log.exception("Ошибка в отложенных")
        try:
            await daily_backup(app)
        except Exception:
            log.exception("Ошибка автобэкапа")
        await asyncio.sleep(TICK)
