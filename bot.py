"""
Главная точка входа приложения.
Инициализирует бота и запускает polling.
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import TOKEN
from middleware import AdminCheckMiddleware, AntiFloodMiddleware, TimezoneMiddleware

# Импортируем роутеры
from start import router as start_router
from orders import router as orders_router
from replies import router as replies_router
from admin import router as admin_router

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Главная асинхронная функция."""

    # Создание бота и диспетчера
    bot = Bot(token=TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Подключение middleware в правильном порядке
    dp.update.middleware(TimezoneMiddleware())
    dp.update.middleware(AdminCheckMiddleware())
    dp.update.middleware(AntiFloodMiddleware())

    # Подключение роутеров
    # Порядок важен: сначала специфичные, потом общие
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(orders_router)
    dp.include_router(replies_router)

    logger.info("🚀 Бот запускается...")

    try:
        # Запуск polling
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
