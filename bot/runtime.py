"""Запуск основного бота и автопереключение на запасной токен.

Основной бот опрашивается тем же циклом, что и зеркала. Telegram ответил Unauthorized (бот заблокирован,
удалён или токен отозван): берётся первый готовый запасной токен, бот продолжает работать, а владельцы
получают сообщение с вопросом, разослать ли зеркалам новый адрес."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.methods import GetUpdates
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup

from .app import App
from .config import Config
from .crypto import TokenCipher
from .mirrors import classify_error, token_bot_id
from .polling import PollLoop
from .reserves import env_hash
from .store import now

log = logging.getLogger(__name__)

RETRY_NO_RESERVE = 60  # нет живых запасных: проверяем снова раз в минуту


@dataclass
class FailoverInfo:
    ts: int
    old_username: str
    new_username: str
    reason: str
    manual: bool = False


@dataclass
class RunnerState:
    down: bool = False                 # основной не работает и запасных нет
    last: FailoverInfo | None = None
    history: list[FailoverInfo] = field(default_factory=list)


class MainRunner:
    def __init__(self, app: App, dp: Dispatcher, factory: Callable[[str], Bot], polling_timeout: int = 30) -> None:
        self.app = app
        self.dp = dp
        self.factory = factory
        self.state = RunnerState()
        self._timeout = polling_timeout
        self._limiter = asyncio.Semaphore(200)
        self._lock = asyncio.Lock()
        self._loop: PollLoop | None = None
        self._retry: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._allowed = dp.resolve_used_update_types()

    # ---------- цикл ----------
    def start(self) -> None:
        self._loop = self._new_loop()
        self._loop.start()

    def _new_loop(self) -> PollLoop:
        async def fetch(offset: int | None, timeout: int) -> Any:
            bot = self.app.main_bot
            method = GetUpdates(offset=offset, timeout=timeout, allowed_updates=self._allowed)
            return await bot(method, request_timeout=int(bot.session.timeout + timeout))

        async def handle(update: Any) -> Any:
            return await self.dp.feed_update(self.app.main_bot, update)

        async def on_fatal(reason: str) -> None:
            log.critical("Токен основного бота не работает: %s", reason)
            await self.failover(reason, from_loop=True)

        return PollLoop("main", fetch=fetch, handle=handle, limiter=self._limiter, on_error=classify_error,
                        on_fatal=on_fatal, polling_timeout=self._timeout)

    @property
    def running(self) -> bool:
        return self._loop is not None and self._loop.running

    async def stop(self) -> None:
        if self._retry is not None:
            self._retry.cancel()
        if self._loop is not None:
            await self._loop.stop()
        for t in list(self._tasks):
            t.cancel()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---------- переключение ----------
    async def failover(self, reason: str, *, from_loop: bool = False, manual: bool = False) -> FailoverInfo | None:
        """Переключиться на первый живой запасной токен. None - переключаться не на что."""
        async with self._lock:
            app = self.app
            picked: tuple[Bot, Any] | None = None
            for bot_id, token in await app.reserves.candidates():
                bot = self.factory(token)
                try:
                    me = await bot.get_me()
                except TelegramUnauthorizedError:
                    await app.reserves.mark(bot_id, "dead")
                    log.warning("Запасной бот %s не принят Telegram, помечен нерабочим", bot_id)
                    continue
                except TelegramAPIError as e:
                    log.warning("Запасной бот %s не проверился: %s", bot_id, e)
                    continue
                picked = (bot, me)
                break

            if picked is None:
                self.state.down = True
                await app.log_event("🔴 Основной бот не работает, а живых запасных токенов нет. "
                                    "Добавьте запасной в веб-панели.")
                if self._retry is None or self._retry.done():
                    self._retry = asyncio.create_task(self._retry_loop(reason))
                return None

            new_bot, me = picked
            old_username = app.bot_username
            if manual and self._loop is not None and not from_loop:
                await self._loop.stop()  # из своего же цикла остановка не нужна: он сам завершится
            app.main_bot = new_bot
            app.bot_username = me.username or ""
            await app.media.load(new_bot.id)
            await app.set_setting("last_bot_id", new_bot.id)
            await app.reserves.activate(new_bot.id)
            # Готовые ссылки старого бота новый отозвать не может: убираем их из запаса, пул наполнится заново.
            await app.db.execute("UPDATE invite_links SET revoked = 1 WHERE user_id IS NULL AND revoked = 0")
            app.links.pool_counts.clear()
            app.links.wake.set()
            self.state.down = False
            info = FailoverInfo(now(), old_username, app.bot_username, reason, manual)
            self.state.last = info
            self.state.history = [*self.state.history[-9:], info]
            self._loop = self._new_loop()
            self._loop.start()
            try:
                await new_bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
            except TelegramAPIError:
                pass
            self._spawn(self._after_switch(info))
            log.warning("Основной бот переключён: @%s -> @%s (%s)", old_username, app.bot_username, reason)
            return info

    async def _retry_loop(self, reason: str) -> None:
        while self.state.down:
            await asyncio.sleep(RETRY_NO_RESERVE)
            try:
                if await self.failover(reason, from_loop=True):
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка повторного переключения")

    async def _after_switch(self, info: FailoverInfo) -> None:
        app = self.app
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, отправить", callback_data=f"mn:y:{app.main_bot.id}"),
            InlineKeyboardButton(text="Нет", callback_data="mn:n"),
        ]])
        old = f"@{escape(info.old_username)}" if info.old_username else "прежний бот"
        how = "Переключено вручную" if info.manual else "Основной бот перестал отвечать, включён запасной"
        await app.notify_admins(
            f"🔁 <b>{how}</b>\n{old} → @{escape(info.new_username)}\nПричина: {escape(info.reason)}\n\n"
            "Отправить зеркалам и их пользователям сообщение о новом адресе?", perm=None, reply_markup=kb)
        await app.log_event(f"🔁 Основной бот: {old} → @{escape(info.new_username)} ({escape(info.reason)}).")
        try:
            await app.links.check_chats()  # сразу видно, где запасной бот не админ
        except Exception:
            log.exception("Проверка чатов после переключения")
        if app.media.pending_warmup():
            owner = min(app.config.owner_ids)
            try:
                await app.media.warmup(app.main_bot, owner)
            except Exception:
                log.exception("Прогрев медиа после переключения")


# ---------- выбор токена при запуске ----------
def _read_state(config: Config) -> tuple[dict | None, list[tuple[int, bytes]]]:
    path = config.db_path
    if not path.exists():
        return None, []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None, []
    try:
        row = con.execute("SELECT value FROM settings WHERE key = 'main_override'").fetchone()
        override = json.loads(row[0]) if row and row[0] else None
        reserves = con.execute(
            "SELECT bot_id, token_enc FROM reserve_tokens WHERE status IN ('active', 'ready') "
            "ORDER BY (status = 'active') DESC, position").fetchall()
        return override, [(r[0], bytes(r[1])) for r in reserves]
    except sqlite3.Error:
        return None, []
    finally:
        con.close()


async def pick_main_token(config: Config, factory: Callable[[str], Bot]) -> str:
    """Токен для запуска. Обычно из .env. Если раньше сработало переключение, берётся выбранный запасной,
    а если .env давно не работает, первый живой запасной. Ничего не подошло: отдаём токен из .env как есть."""
    cipher = TokenCipher(config.secret_key) if config.secret_key else None
    override, reserves = await asyncio.to_thread(_read_state, config)
    candidates: list[str] = []
    stored: dict[int, str] = {}
    if cipher is not None:
        for bot_id, blob in reserves:
            try:
                stored[bot_id] = cipher.decrypt(blob)
            except Exception:
                continue
    if (override and override.get("env") == env_hash(config.bot_token)
            and override.get("bot_id") in stored):
        candidates.append(stored[override["bot_id"]])
    candidates.append(config.bot_token)
    candidates += [t for t in stored.values() if t not in candidates]

    for token in candidates:
        bot = factory(token)
        try:
            await bot.get_me()
            return token
        except TelegramUnauthorizedError:
            log.warning("Токен бота %s не принят Telegram, пробую следующий", token.split(":")[0])
        except TelegramAPIError:
            return token  # сеть или Telegram недоступны: дальше разберётся обычный запуск
    return config.bot_token


async def sync_override(app: App) -> None:
    """После запуска согласуем записи: какой бот сейчас основной, из .env он или запасной."""
    env_id = token_bot_id(app.config.bot_token)
    current = app.main_bot.id
    if current != env_id and await app.reserves.has(current):
        await app.reserves.activate(current)
    elif current == env_id and app.store.setting("main_override", None):
        await app.set_setting("main_override", None)
