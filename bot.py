"""
Главная точка входа приложения.
Инициализирует бота и запускает polling.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from config import TOKEN, PROXY
from middleware import AntiFloodMiddleware
from handlers.panel import router as panel_router
from handlers.admin import router as admin_router
from handlers.group import router as group_router
from handlers.user import router as user_router

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    # Через прокси (например, туннель на зарубежный сервер), если он задан в .env
    session = AiohttpSession(proxy=PROXY) if PROXY else AiohttpSession()
    if PROXY:
        logger.info("Запросы к Telegram идут через прокси %s", PROXY)
    bot = Bot(token=TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Антифлуд только для пользователей в личке — рабочая группа без ограничений
    user_router.message.middleware(AntiFloodMiddleware())

    # Порядок важен: админ-панель -> команды админа -> рабочая группа -> пользователи
    dp.include_router(panel_router)
    dp.include_router(admin_router)
    dp.include_router(group_router)
    dp.include_router(user_router)

    logger.info("🚀 Бот запускается...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
