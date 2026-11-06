"""Цикл получения обновлений одного бота.

Своя реализация вместо dp.start_polling: тот принимает список ботов один раз при старте,
а клоны должны подключаться и отключаться на ходу, без перезапуска остальных.
Семафор общий на всех ботов, поэтому один шумный чат не раздует память процесса.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ErrorAction:
    fatal: bool = False
    delay: float | None = None  # None: обычная нарастающая пауза
    reason: str = ""


class PollLoop:
    def __init__(
        self,
        name: str,
        *,
        fetch: Callable[[int | None, int], Awaitable[Sequence[Any]]],
        handle: Callable[[Any], Awaitable[Any]],
        limiter: asyncio.Semaphore,
        on_error: Callable[[Exception], ErrorAction],
        on_fatal: Callable[[str], Awaitable[None]] | None = None,
        polling_timeout: int = 30,
        backoff_min: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        self.name = name
        self._fetch = fetch
        self._handle = handle
        self._limiter = limiter
        self._on_error = on_error
        self._on_fatal = on_fatal
        self._polling_timeout = polling_timeout
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max
        self._offset: int | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._main: asyncio.Task[None] | None = None
        self.processed = 0

    @property
    def running(self) -> bool:
        return self._main is not None and not self._main.done()

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    def start(self) -> None:
        self._main = asyncio.create_task(self._run(), name=f"poll:{self.name}")

    async def _run(self) -> None:
        delay = self._backoff_min
        while True:
            try:
                updates = await self._fetch(self._offset, self._polling_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                action = self._on_error(exc)
                if action.fatal:
                    log.error("Бот %s остановлен: %s", self.name, action.reason or exc)
                    if self._on_fatal is not None:
                        await self._on_fatal(action.reason or str(exc))
                    return
                if action.delay is not None:
                    wait = action.delay
                else:
                    wait = delay * random.uniform(0.8, 1.2)
                    delay = min(delay * 2, self._backoff_max)
                log.warning(
                    "Бот %s: не получил обновления (%s), пауза %.1f с",
                    self.name, type(exc).__name__, wait,
                )
                await asyncio.sleep(wait)
                continue
            delay = self._backoff_min
            for update in updates:
                await self._limiter.acquire()
                # Сдвигаем только после получения слота: отмена в ожидании не теряет событие.
                self._offset = update.update_id + 1
                task = asyncio.create_task(self._process(update))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _process(self, update: Any) -> None:
        try:
            await self._handle(update)
        except Exception:
            log.exception("Бот %s: ошибка при обработке события", self.name)
        finally:
            self.processed += 1
            self._limiter.release()

    async def stop(self, *, drain_timeout: float = 10.0) -> None:
        if self._main is not None and not self._main.done():
            self._main.cancel()
            try:
                await self._main
            except asyncio.CancelledError:
                pass
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=drain_timeout)
        if self._offset is not None:
            # Подтверждаем Telegram обработанное, иначе после перезапуска события придут снова
            # и одно нарушение накажут дважды.
            try:
                await asyncio.wait_for(self._fetch(self._offset, 0), timeout=5)
            except Exception as exc:  # noqa: BLE001
                log.warning("Бот %s: не подтвердил обновления: %s", self.name, exc)
