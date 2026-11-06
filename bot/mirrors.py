"""Зеркала: боты других людей с теми же кнопками и ссылками.

Человек присылает токен своего бота, и тот начинает работать как зеркало. Всё крутится в одном процессе:
обновили код, перезапустили - обновились сразу все зеркала. Ссылки по-прежнему создаёт только основной бот
(он админ в каналах), зеркало лишь показывает их людям, поэтому зеркалу права в каналах не нужны.

Зеркало отвечает только пользовательской частью: админки и обработки чатов у него нет.
Токены хранятся зашифрованными (SECRET_KEY), без ключа зеркала выключены.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramConflictError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetUpdates
from aiogram.types import BotCommand
from aiogram.utils.token import TokenValidationError, validate_token

from .app import App, use_bot
from .polling import ErrorAction, PollLoop
from .store import now

log = logging.getLogger(__name__)

MIRROR_UPDATES = ["message", "callback_query"]
DEFAULT_PER_USER = 1
DEFAULT_MAX_TOTAL = 200


class MirrorError(Exception):
    """Понятная человеку причина, почему зеркало не подключилось."""


@dataclass(slots=True)
class Mirror:
    bot_id: int
    username: str
    owner_id: int
    status: str = "active"


def classify_error(exc: Exception) -> ErrorAction:
    if isinstance(exc, TelegramUnauthorizedError):
        return ErrorAction(fatal=True, reason="токен отозван или бот удалён")
    if isinstance(exc, TelegramRetryAfter):
        return ErrorAction(delay=float(exc.retry_after))
    if isinstance(exc, TelegramConflictError):
        return ErrorAction(delay=5.0)
    return ErrorAction()


def token_bot_id(token: str) -> int:
    return int(token.split(":", 1)[0])


def make_session() -> AiohttpSession:
    return AiohttpSession(limit=256)


class BotFactory:
    """Создаёт Bot с общим HTTP-соединением. В тестах подменяется."""

    def __init__(self, session: AiohttpSession | None = None) -> None:
        self.session = session

    def __call__(self, token: str) -> Bot:
        if self.session is None:
            self.session = make_session()
        return Bot(token, session=self.session,
                   default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()


class MirrorManager:
    def __init__(self, app: App, make_dp: Callable[[], Dispatcher], factory: Callable[[str], Bot] | None = None,
                 polling_timeout: int = 30) -> None:
        self.app = app
        self._make_dp = make_dp
        self._dp: Dispatcher | None = None
        self.factory = factory or BotFactory()
        self._timeout = polling_timeout
        self._limiter = asyncio.Semaphore(200)
        self._running: dict[int, tuple[Bot, PollLoop]] = {}
        self._lock = asyncio.Lock()
        self.owners: set[int] = set()  # у кого есть зеркало: от этого зависит кнопка в меню

    # ---------- состояние ----------
    @property
    def enabled(self) -> bool:
        return self.app.cipher is not None

    @property
    def public(self) -> bool:
        """Показывать людям кнопку «Создать зеркало»."""
        return self.enabled and bool(self.app.store.setting("mirrors_public", 1))

    def owns(self, user_id: int) -> bool:
        return user_id in self.owners

    def visible_for(self, user_id: int) -> bool:
        """Показывать ли человеку кнопку про зеркало: создать (если можно) или управлять своим."""
        return self.enabled and (self.public or self.owns(user_id))

    async def load_owners(self) -> None:
        self.owners = {r["owner_id"] for r in await self.app.db.fetchall("SELECT DISTINCT owner_id FROM mirrors")}

    async def of_owner(self, owner_id: int) -> list[dict]:
        rows = await self.app.db.fetchall(
            "SELECT bot_id, username, status FROM mirrors WHERE owner_id = ? ORDER BY created_at", (owner_id,))
        return [{**dict(r), "running": self.is_running(r["bot_id"])} for r in rows]

    @property
    def dp(self) -> Dispatcher:
        if self._dp is None:
            self._dp = self._make_dp()
        return self._dp

    def running_bots(self) -> list[Bot]:
        return [bot for bot, _ in self._running.values()]

    def running_ids(self) -> list[int]:
        return [bot_id for bot_id in self._running if self.is_running(bot_id)]

    def bot_of(self, bot_id: int) -> Bot | None:
        item = self._running.get(bot_id)
        return item[0] if item and self.is_running(bot_id) else None

    def is_running(self, bot_id: int) -> bool:
        item = self._running.get(bot_id)
        return item is not None and item[1].running

    async def listing(self) -> list[dict]:
        rows = await self.app.db.fetchall(
            "SELECT m.bot_id, m.username, m.owner_id, m.status, m.created_at, "
            "(SELECT COUNT(*) FROM user_bots u WHERE u.bot_id = m.bot_id) AS users "
            "FROM mirrors m ORDER BY m.created_at")
        return [{**dict(r), "running": self.is_running(r["bot_id"])} for r in rows]

    # ---------- запуск ----------
    async def start_all(self) -> None:
        if not self.enabled:
            return
        await self.load_owners()
        broken = 0
        for r in await self.app.db.fetchall("SELECT bot_id, token_enc FROM mirrors WHERE status = 'active'"):
            try:
                token = self.app.cipher.decrypt(r["token_enc"])  # type: ignore[union-attr]
            except Exception:
                log.error("Не удалось расшифровать токен зеркала %s: SECRET_KEY другой?", r["bot_id"])
                broken += 1
                continue
            await self._start(self.factory(token))
        if broken:
            await self.app.alert(f"⚠️ Не удалось расшифровать токены зеркал ({broken}): SECRET_KEY в .env не тот, "
                                 "которым они были сохранены. Верните прежний ключ и перезапустите бот.")

    async def _start(self, bot: Bot) -> None:
        bot_id = bot.id
        old = self._running.pop(bot_id, None)
        if old is not None:
            await old[1].stop()
        session = bot.session

        async def fetch(offset: int | None, timeout: int) -> Any:
            method = GetUpdates(offset=offset, timeout=timeout, allowed_updates=MIRROR_UPDATES)
            return await bot(method, request_timeout=int(session.timeout + timeout))

        async def handle(update: Any) -> Any:
            with use_bot(bot):
                return await self.dp.feed_update(bot, update)

        async def on_fatal(reason: str) -> None:
            await self._revoked(bot_id, reason)

        loop = PollLoop(f"mirror:{bot_id}", fetch=fetch, handle=handle, limiter=self._limiter,
                        on_error=classify_error, on_fatal=on_fatal, polling_timeout=self._timeout)
        self._running[bot_id] = (bot, loop)
        loop.start()
        try:
            await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
        except TelegramAPIError:
            pass

    async def _revoked(self, bot_id: int, reason: str) -> None:
        self._running.pop(bot_id, None)
        row = await self.app.db.fetchone("SELECT username, owner_id FROM mirrors WHERE bot_id = ?", (bot_id,))
        await self.app.db.execute("UPDATE mirrors SET status = 'revoked' WHERE bot_id = ?", (bot_id,))
        if row is None:
            return
        name = f"@{escape(row['username'] or str(bot_id))}"
        await self.app.log_event(f"🪞 Зеркало {name} отключено: {escape(reason)}.")
        try:
            await self.app.main_bot.send_message(
                row["owner_id"], f"⚠️ Ваше зеркало {name} перестало работать: {escape(reason)}. "
                                 "Пришлите новый токен, и оно запустится снова.")
        except TelegramAPIError:
            pass

    async def stop_all(self) -> None:
        items, self._running = list(self._running.values()), {}
        await asyncio.gather(*(loop.stop() for _, loop in items), return_exceptions=True)
        close = getattr(self.factory, "close", None)
        if close is not None:
            await close()

    # ---------- добавить и убрать ----------
    async def add(self, token: str, owner_id: int, replacing: bool = False) -> Mirror:
        """replacing: человек меняет своё зеркало - прежние убираются после того, как новое успешно запущено."""
        app = self.app
        if not self.enabled:
            raise MirrorError("Зеркала сейчас выключены.")
        token = token.strip()
        try:
            validate_token(token)
        except TokenValidationError as e:
            raise MirrorError("Это не похоже на токен бота. Он выглядит так: 123456789:AA...") from e
        bot_id = token_bot_id(token)

        async with self._lock:
            if bot_id == app.main_bot.id or await app.reserves.has(bot_id):
                raise MirrorError("Этот токен принадлежит основному боту. Создайте отдельного бота в @BotFather.")
            existing = await app.db.fetchone("SELECT status FROM mirrors WHERE bot_id = ?", (bot_id,))
            if existing is not None and existing["status"] == "active" and self.is_running(bot_id):
                raise MirrorError("Этот бот уже подключён как зеркало.")
            limit = int(app.store.setting("mirrors_per_user", DEFAULT_PER_USER))
            mine = 0 if replacing else await app.db.fetchval(
                "SELECT COUNT(*) FROM mirrors WHERE owner_id = ? AND status = 'active' AND bot_id != ?",
                (owner_id, bot_id))
            if limit > 0 and mine >= limit and owner_id not in app.config.owner_ids:
                raise MirrorError("У вас уже есть зеркало, а можно одно на человека." if limit == 1
                                  else f"Можно не больше {limit} зеркал на человека.")
            total = await app.db.fetchval("SELECT COUNT(*) FROM mirrors WHERE status = 'active'")
            if total >= int(app.store.setting("mirrors_max", DEFAULT_MAX_TOTAL)):
                raise MirrorError("Достигнут предел числа зеркал, попробуйте позже.")

            bot = self.factory(token)
            try:
                me = await bot.get_me()
            except TelegramUnauthorizedError as e:
                raise MirrorError("Telegram не принял токен. Проверьте его в @BotFather: возможно, он отозван.") from e
            except TelegramAPIError as e:
                raise MirrorError("Не получилось проверить токен, попробуйте ещё раз через минуту.") from e
            username = me.username or str(me.id)
            await app.db.execute(
                "INSERT INTO mirrors(bot_id, username, token_enc, owner_id, status, created_at) "
                "VALUES (?, ?, ?, ?, 'active', ?) ON CONFLICT(bot_id) DO UPDATE SET username = excluded.username, "
                "token_enc = excluded.token_enc, owner_id = excluded.owner_id, status = 'active'",
                (bot_id, username, app.cipher.encrypt(token), owner_id, now()))  # type: ignore[union-attr]
            await self._start(bot)
            if replacing:
                for old in await app.db.fetchall(
                        "SELECT bot_id FROM mirrors WHERE owner_id = ? AND bot_id != ?", (owner_id, bot_id)):
                    await self._drop(old["bot_id"])
            self.owners.add(owner_id)
            app.backup_soon()
        await app.log_event(f"🪞 Новое зеркало @{escape(username)} (владелец <code>{owner_id}</code>).")
        return Mirror(bot_id, username, owner_id)

    async def _drop(self, bot_id: int) -> bool:
        item = self._running.pop(bot_id, None)
        if item is not None:
            await item[1].stop()
        removed = bool(await self.app.db.execute_rowcount("DELETE FROM mirrors WHERE bot_id = ?", (bot_id,)))
        await self.load_owners()
        self.app.backup_soon()
        return removed

    async def remove(self, bot_id: int) -> bool:
        async with self._lock:
            return await self._drop(bot_id)
