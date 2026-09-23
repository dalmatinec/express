"""
Общие помощники для обработчиков.
"""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from aiogram.exceptions import TelegramRetryAfter

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retry(call: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    """Повторить запрос к Telegram, если упёрлись в лимит (429 Too Many Requests)."""
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramRetryAfter as e:
            if attempt == attempts - 1:
                raise
            logger.warning("Лимит Telegram, ждём %s сек.", e.retry_after)
            await asyncio.sleep(e.retry_after)
