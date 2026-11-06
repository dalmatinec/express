"""Уведомление зеркалам и их пользователям: у основного бота новый адрес.

Каждый бот пишет только своим людям (те, кто когда-то его запускал и не заблокировал): Telegram не даёт
боту писать первым. Поэтому рассылка идёт через основной бот и через каждое работающее зеркало."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

from .app import App
from .store import now

log = logging.getLogger(__name__)

PAUSE = 0.05  # ~20 сообщений в секунду на бота


@dataclass
class NotifyState:
    text: str
    started: int = field(default_factory=now)
    finished: int = 0
    total: int = 0
    sent: int = 0
    blocked: int = 0
    failed: int = 0
    bots: int = 0

    def summary(self) -> str:
        done = "готово" if self.finished else "идёт"
        return (f"{done}: отправлено {self.sent} из {self.total}, заблокировали бота {self.blocked}, "
                f"ошибок {self.failed}, ботов {self.bots}")


def default_text(username: str) -> str:
    return (f"⚠️ Основной бот сменил адрес.\nНовый адрес: @{username}\n\n"
            "Зеркала работают как раньше.")


class MirrorNotifier:
    def __init__(self, app: App) -> None:
        self.app = app
        self.state: NotifyState | None = None
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, text: str) -> bool:
        """Текст - обычный, без разметки: мы сами его экранируем."""
        if self.running:
            return False
        self.state = NotifyState(text=text)
        self._task = asyncio.create_task(self._run(self.state))
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await self._task

    async def _run(self, state: NotifyState) -> None:
        bots = [self.app.main_bot, *self.app.mirrors.running_bots()]
        state.bots = len(bots)
        html = escape(state.text)[:4000]
        audiences: list[tuple[Bot, list[int]]] = []
        for bot in bots:
            rows = await self.app.db.fetchall(
                "SELECT user_id FROM user_bots WHERE bot_id = ? AND is_blocked = 0", (bot.id,))
            audiences.append((bot, [r["user_id"] for r in rows]))
        state.total = sum(len(ids) for _, ids in audiences)
        try:
            await asyncio.gather(*(self._one(bot, ids, html, state) for bot, ids in audiences))
        finally:
            state.finished = now()
            await self.app.log_event(f"📣 Уведомление зеркалам: {state.summary()}.")

    async def _one(self, bot: Bot, ids: list[int], html: str, state: NotifyState) -> None:
        for uid in ids:
            for attempt in (0, 1):
                try:
                    await bot.send_message(uid, html, disable_notification=False)
                    state.sent += 1
                except TelegramRetryAfter as e:
                    if attempt == 0:
                        await asyncio.sleep(e.retry_after + 1)
                        continue
                    state.failed += 1
                except TelegramForbiddenError:
                    state.blocked += 1
                    await self.app.db.execute(
                        "UPDATE user_bots SET is_blocked = 1 WHERE user_id = ? AND bot_id = ?", (uid, bot.id))
                except TelegramAPIError:
                    state.failed += 1
                break
            await asyncio.sleep(PAUSE)
