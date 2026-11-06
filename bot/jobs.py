"""Фоновые задачи: сброс буферов, отзыв протухших ссылок, сроки рекламы, бэкап."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile

from .app import App
from .backup import make_backup
from .middlewares import GuardMiddleware
from .store import now

log = logging.getLogger(__name__)

TICK = 30
MAINTENANCE_EVERY = 60
CHAT_CHECK_EVERY = 15 * 60
LOG_KEEP_DAYS = 90
SEND_LIMIT = 49 * 1024 * 1024  # бот может отправить файл до 50 МБ


async def run_jobs(app: App, guard: GuardMiddleware) -> None:
    ticks = 0
    while True:
        await asyncio.sleep(TICK)
        ticks += TICK
        try:
            await app.flush()
            if ticks % MAINTENANCE_EVERY == 0:
                guard.cleanup()
                await app.links.cleanup()
                await app.ads.check_schedule()
                await expire_bans(app)
                await daily(app)
                await daily_summary(app)
            if ticks % CHAT_CHECK_EVERY == 0:
                await app.links.check_chats()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка фоновой задачи")


async def expire_bans(app: App) -> None:
    t = now()
    for uid in [u for u, until in app.banned.items() if until is not None and until <= t]:
        await app.unban(uid)


async def daily(app: App) -> None:
    s = app.store.setting
    local = datetime.now(timezone(timedelta(hours=int(s("tz_offset", 3)))))
    today = local.strftime("%Y-%m-%d")
    if local.hour < int(s("backup_hour", 4)) or s("last_backup_day", "") == today:
        return
    await app.set_setting("last_backup_day", today)
    await app.db.execute("DELETE FROM admin_log WHERE ts < ?", (now() - LOG_KEEP_DAYS * 86400,))
    await app.db.execute("PRAGMA optimize")
    await send_backup(app, "💾 Ежедневный бэкап")
    removed = await app.media.collect_garbage()
    if removed:
        log.info("Удалено неиспользуемых медиа: %s", removed)


async def summary_text(app: App) -> str:
    db = app.db
    since = now() - 86400
    users = await db.fetchval("SELECT COUNT(*) FROM users")
    new = await db.fetchval("SELECT COUNT(*) FROM users WHERE created_at >= ?", (since,))
    active = await db.fetchval("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (since,))
    lines = ["📊 <b>Сводка за сутки</b>", "",
             f"Людей всего: <b>{users}</b>, новых: <b>{new}</b>, заходили: <b>{active}</b>"]
    if app.mirrors is not None and app.mirrors.enabled:
        per_bot = await db.fetchall(
            "SELECT bot_id, COUNT(*) AS n FROM user_bots WHERE is_blocked = 0 GROUP BY bot_id")
        main_n = sum(r["n"] for r in per_bot if r["bot_id"] == app.main_bot.id)
        mirror_n = sum(r["n"] for r in per_bot if r["bot_id"] != app.main_bot.id)
        listing = await app.mirrors.listing()
        alive = sum(1 for m in listing if m.get("running"))
        lines.append(f"Через основного бота: <b>{main_n}</b>, через зеркала: <b>{mirror_n}</b>")
        lines.append(f"Зеркал работает: <b>{alive}</b> из {len(listing)}")
        reserves = await app.reserves.listing()
        ready = sum(1 for r in reserves if r.status == "ready")
        warn = " ⚠️ запасных нет, добавьте в веб-панели" if ready == 0 else ""
        lines.append(f"Запасных токенов готово: <b>{ready}</b>{warn}")
    lines.append(f"Готовых ссылок в запасе: <b>{sum(app.links.pool_counts.values())}</b>")
    return "\n".join(lines)


async def daily_summary(app: App) -> None:
    s = app.store.setting
    local = datetime.now(timezone(timedelta(hours=int(s("tz_offset", 3)))))
    today = local.strftime("%Y-%m-%d")
    if local.hour < int(s("summary_hour", 9)) or s("last_summary_day", "") == today:
        return
    await app.set_setting("last_summary_day", today)
    text = await summary_text(app)
    for uid in sorted(app.config.owner_ids):
        try:
            await app.main_bot.send_message(uid, text, disable_notification=True)
        except TelegramAPIError as e:
            log.warning("Сводку не доставить %s: %s", uid, e)


async def send_backup(app: App, caption: str) -> str:
    """Бэкап на сервер (там лежат 3 последних) + копия в канал логов, а без канала - владельцам."""
    path = await make_backup(app)
    size = f"{path.stat().st_size / 1048576:.1f} МБ"
    on_server = f"На сервере: <code>{escape(str(path.parent.resolve()))}</code>"
    if path.stat().st_size > SEND_LIMIT:
        await app.log_event(f"{caption}: {escape(path.name)} ({size}), в Telegram не влезает.\n{on_server}")
        return f"✅ Бэкап готов, {size}. В Telegram не влезает, забирайте с сервера.\n{on_server}"
    data = await asyncio.to_thread(path.read_bytes)
    targets = [app.log_chat] if app.log_chat else sorted(app.config.owner_ids)
    errors = []
    for target in targets:
        try:
            await app.bot.send_document(target, BufferedInputFile(data, filename=path.name),
                                        caption=f"{caption} ({size})", disable_notification=True)
        except TelegramAPIError as e:
            errors.append(str(e))
    if errors:
        return f"⚠️ Бэкап готов ({size}), но в Telegram не ушёл: {escape(errors[0])[:200]}\n{on_server}"
    return f"✅ Бэкап готов, {size}, отправлен {'в канал логов' if app.log_chat else 'владельцу в личку'}.\n{on_server}"


