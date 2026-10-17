"""Запуск: python -m postbot"""
import asyncio
import logging
from html import escape

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from .app import App
from .config import Config, load_config
from .db import Database
from .handlers import build_router
from .middlewares import AccessMiddleware
from .services.jobs import run_jobs
from .services.media import MediaStore
from .services.publisher import channel_rights, recover_sending

log = logging.getLogger("postbot")


async def setup(config: Config, bot: Bot) -> tuple[App, Dispatcher]:
    """Собирает всё вместе. Отдельно от main, чтобы тесты могли поднять бота без сети."""
    db = Database(config.db_path)
    await db.connect()
    app = App(config, db, bot)
    await app.load()
    me = await bot.get_me()
    app.bot_id, app.bot_username = me.id, me.username or ""
    app.media = MediaStore(app)
    await recover_sending(app)

    dp = Dispatcher(storage=MemoryStorage(), app=app)
    access = AccessMiddleware(app)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(build_router())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        if isinstance(event.exception, TelegramBadRequest) and "query is too old" in event.exception.message:
            return True
        log.exception("Ошибка при обработке", exc_info=event.exception)
        return True

    return app, dp


async def check_new_token(app: App) -> None:
    """Если бот запущен с новым токеном (например после переезда), проверяем каналы и пишем владельцам."""
    old = app.setting("bot_id")
    if old == app.bot_id:
        return
    await app.set_setting("bot_id", app.bot_id)
    if not old:
        return
    bad = []
    for c in await app.all_channels():
        fresh, _ = await channel_rights(app, c.id)
        if fresh is None or not fresh.ok:
            bad.append(escape(c.title))
    text = f"🔁 Бот запущен с новым токеном: @{escape(app.bot_username)}. Посты, кнопки и история на месте."
    if bad:
        text += "\n\n⚠️ Добавьте этого бота админом в каналы:\n" + "\n".join(f"• {t}" for t in bad)
    await app.notify_owners(text)


async def main() -> None:
    config = load_config()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    app, dp = await setup(config, bot)
    # если этот токен раньше работал на другом сервере через вебхук, Telegram не отдаст обновления,
    # пока вебхук не снят. Тогда бот висит активным, но молчит
    info = await bot.get_webhook_info()
    if info.url:
        log.warning("У бота был вебхук %s, снимаю", info.url)
        await bot.delete_webhook()
    log.info("Владельцы из .env: %s", ", ".join(map(str, sorted(config.owner_ids))))
    await bot.set_my_commands([BotCommand(command="start", description="Меню"),
                               BotCommand(command="cancel", description="Отменить текущий шаг")])
    await check_new_token(app)
    jobs = asyncio.create_task(run_jobs(app))
    log.info("Бот @%s запущен", app.bot_username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        jobs.cancel()
        await app.db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
