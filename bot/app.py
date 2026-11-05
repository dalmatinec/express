"""Сборка приложения: общие сервисы и диспетчер."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

from .admins import AdminCache
from .chats import ChatRepo
from .config import Settings
from .db.pool import Database
from .fsm_store import FsmStore
from .handlers import common, moderation
from .middlewares import PrivateGateMiddleware, TrackingMiddleware
from .moderation.repo import PunishmentRepo
from .moderation.service import Moderation
from .stats import StatsBuffer
from .storage import PgStorage

log = logging.getLogger(__name__)


async def _fetch_admins(bot: Bot, chat_id: int) -> Any:
    return await bot.get_chat_administrators(chat_id)


@dataclass(slots=True)
class App:
    settings: Settings
    db: Database
    chats: ChatRepo
    stats: StatsBuffer
    fsm: FsmStore
    admins: AdminCache
    moderation: Moderation
    manager: Any = None  # BotManager, задаётся после сборки диспетчера
    started_at: float = field(default_factory=time.monotonic)


def build_app(settings: Settings, db: Database) -> App:
    admins = AdminCache(_fetch_admins)
    return App(
        settings=settings,
        db=db,
        chats=ChatRepo(db),
        stats=StatsBuffer(db, flush_seconds=settings.stats_flush_seconds),
        fsm=FsmStore(db),
        admins=admins,
        moderation=Moderation(PunishmentRepo(db), admins),
    )


async def _on_error(event: ErrorEvent) -> bool:
    log.error("Ошибка обработчика: %r", event.exception, exc_info=event.exception)
    return True


def build_dispatcher(app: App) -> Dispatcher:
    dp = Dispatcher(storage=PgStorage(app.fsm))
    dp["app"] = app
    dp.update.outer_middleware(TrackingMiddleware(app.chats, app.stats))
    dp.update.outer_middleware(PrivateGateMiddleware(app.settings.owner_ids))
    dp.errors.register(_on_error)
    dp.include_router(moderation.router)
    dp.include_router(common.router)
    return dp
