"""Запускает основного бота и клоны в одном процессе с общим HTTP-соединением.

Клон необязателен: это тот же код и та же база, но свой токен, имя и @username.
Новый клон добавляет один цикл опроса, а не отдельный процесс.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramConflictError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetUpdates
from aiogram.types import BotCommand, BotCommandScopeAllChatAdministrators
from aiogram.utils.token import TokenValidationError

from .crypto import TokenCipher
from .db import queries as q
from .polling import ErrorAction, PollLoop

log = logging.getLogger(__name__)

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member", "chat_member"]


# Подсказка команд в Telegram видна только админам групп.
ADMIN_COMMANDS = [
    BotCommand(command="ban", description="Бан: срок и причина по желанию"),
    BotCommand(command="unban", description="Снять бан"),
    BotCommand(command="mute", description="Мут: срок и причина по желанию"),
    BotCommand(command="unmute", description="Снять мут"),
    BotCommand(command="kick", description="Исключить из чата"),
    BotCommand(command="warn", description="Предупреждение"),
    BotCommand(command="unwarn", description="Снять последнее предупреждение"),
    BotCommand(command="warns", description="Сколько предупреждений"),
    BotCommand(command="history", description="История наказаний человека"),
    BotCommand(command="journal", description="Журнал чата"),
    BotCommand(command="protect", description="Защита чата: антифлуд и антиспам"),
    BotCommand(command="whitelist", description="Добавить в белый список"),
    BotCommand(command="unwhitelist", description="Убрать из белого списка"),
    BotCommand(command="whitelisted", description="Показать белый список"),
]


class CloneError(Exception):
    """Понятная пользователю причина, почему клон не подключился."""


@dataclass(slots=True)
class RunningBot:
    bot: Bot
    loop: PollLoop
    is_main: bool


def make_session() -> AiohttpSession:
    kwargs: dict[str, Any] = {"limit": 512}
    try:
        import orjson

        kwargs["json_loads"] = orjson.loads  # разбор обновлений заметно быстрее
    except ImportError:
        pass
    return AiohttpSession(**kwargs)


def classify_error(exc: Exception) -> ErrorAction:
    if isinstance(exc, TelegramUnauthorizedError):
        return ErrorAction(fatal=True, reason="токен отозван")
    if isinstance(exc, TelegramRetryAfter):
        return ErrorAction(delay=float(exc.retry_after))
    if isinstance(exc, TelegramConflictError):
        return ErrorAction(delay=5.0)
    return ErrorAction()


class BotManager:
    def __init__(
        self,
        dp: Dispatcher,
        db: Any,
        *,
        owner_id: int,
        secret_key: str | None,
        polling_timeout: int,
        max_concurrent_updates: int,
        on_main_fatal: Callable[[], None],
        session: AiohttpSession | None = None,
    ) -> None:
        self._dp = dp
        self._db = db
        self._owner_id = owner_id
        self._cipher = TokenCipher(secret_key) if secret_key else None
        self._polling_timeout = polling_timeout
        self._limiter = asyncio.Semaphore(max_concurrent_updates)
        self._on_main_fatal = on_main_fatal
        self._session = session or make_session()
        self._running: dict[int, RunningBot] = {}

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def in_flight(self) -> int:
        return sum(item.loop.in_flight for item in self._running.values())

    def is_main(self, bot_id: int) -> bool:
        item = self._running.get(bot_id)
        return bool(item and item.is_main)

    def _new_bot(self, token: str) -> Bot:
        return Bot(
            token,
            session=self._session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
        )

    def _start_loop(self, bot: Bot, *, is_main: bool, label: str) -> None:
        async def fetch(offset: int | None, timeout: int) -> Any:
            method = GetUpdates(offset=offset, timeout=timeout, allowed_updates=ALLOWED_UPDATES)
            # Ждать ответа надо дольше, чем длится сам длинный опрос.
            request_timeout = int(self._session.timeout + timeout)
            return await bot(method, request_timeout=request_timeout)

        async def handle(update: Any) -> Any:
            return await self._dp.feed_update(bot, update)

        async def on_fatal(reason: str) -> None:
            self._running.pop(bot.id, None)
            if is_main:
                log.critical("Токен основного бота не работает: %s", reason)
                self._on_main_fatal()
            else:
                await self._db.execute(q.SET_BOT_STATUS, bot.id, "revoked")

        loop = PollLoop(
            label,
            fetch=fetch,
            handle=handle,
            limiter=self._limiter,
            on_error=classify_error,
            on_fatal=on_fatal,
            polling_timeout=self._polling_timeout,
        )
        self._running[bot.id] = RunningBot(bot, loop, is_main)
        loop.start()

    async def start(self, main_token: str) -> None:
        bot = self._new_bot(main_token)
        me = await self._get_me_with_retry(bot)
        async with self._db.transaction() as connection:
            await connection.execute(q.DEMOTE_OLD_MAIN, bot.id)
            await connection.execute(
                q.UPSERT_MAIN_BOT, bot.id, me.username, me.first_name, self._owner_id
            )
        await self._set_commands(bot)
        self._start_loop(bot, is_main=True, label=f"@{me.username}")
        log.info("Основной бот @%s запущен", me.username)
        await self._start_clones()

    async def _set_commands(self, bot: Bot) -> None:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeAllChatAdministrators()
            )
        except TelegramAPIError as exc:  # без подсказки команды всё равно работают
            log.warning("Не удалось задать подсказку команд: %s", exc)

    async def _get_me_with_retry(self, bot: Bot) -> Any:
        delay = 2.0
        while True:
            try:
                return await bot.get_me()
            except TelegramUnauthorizedError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram недоступен (%s), повторю через %.0f с", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _start_clones(self) -> None:
        rows = await self._db.fetch(q.LIST_ACTIVE_CLONES)
        if not rows:
            return
        if self._cipher is None:
            log.error("Есть клоны (%d), но не задан SECRET_KEY: они не запущены", len(rows))
            return
        for row in rows:
            try:
                token = self._cipher.decrypt(row["token_enc"])
            except Exception:  # noqa: BLE001
                # Сбой одного клона не должен мешать остальным.
                log.exception("Не удалось расшифровать токен клона %s", row["id"])
                continue
            self._start_loop(self._new_bot(token), is_main=False, label=f"@{row['username']}")
        log.info("Запущено клонов: %d", self.running_count - 1)

    async def add_clone(self, token: str, owner_id: int) -> Any:
        """Проверяет токен, сохраняет клона и сразу запускает. Возвращает данные бота.

        Клоны платные: вызывать только после подтверждения оплаты или решением владельца.
        """
        if self._cipher is None:
            raise CloneError("Клоны выключены: на сервере не задан SECRET_KEY.")
        try:
            bot = self._new_bot(token.strip())
        except TokenValidationError as exc:
            raise CloneError("Это не похоже на токен бота.") from exc
        if bot.id in self._running:
            raise CloneError("Этот бот уже подключён.")
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError as exc:
            raise CloneError("Telegram не принял токен. Проверьте его у @BotFather.") from exc
        row = await self._db.fetchrow(
            q.UPSERT_CLONE,
            bot.id, me.username, me.first_name, owner_id, self._cipher.encrypt(token.strip()),
        )
        if row is None:
            raise CloneError("Этот токен принадлежит основному боту.")
        await self._set_commands(bot)
        self._start_loop(bot, is_main=False, label=f"@{me.username}")
        return me

    async def remove_clone(self, bot_id: int) -> None:
        item = self._running.get(bot_id)
        if item is not None and item.is_main:
            raise CloneError("Основного бота отключить нельзя.")
        await self._db.execute(q.SET_BOT_STATUS, bot_id, "disabled")
        if item is not None:
            self._running.pop(bot_id, None)
            await item.loop.stop()

    async def stop(self) -> None:
        items = list(self._running.values())
        self._running.clear()
        await asyncio.gather(*(item.loop.stop() for item in items), return_exceptions=True)
        await self._session.close()
