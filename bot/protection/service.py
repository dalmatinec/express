"""Решает, что делать с сообщением. На обычном сообщении обходится без базы и сети."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..cache import TTLCache
from ..db import queries as q
from ..moderation.cards import mention, render_card, undo_target
from ..moderation.service import Moderation, ModerationError, ModSettings, Person
from ..texts import t
from .detector import MessageView, find_violation, normalize
from .settings import ProtectionSettings
from .tracker import Tracker

log = logging.getLogger(__name__)

AUTO_ADMIN = Person(0, "бот")
# Telegram разрешает боту около 20 сообщений в минуту в группу.
NOTICE_GAP_SECONDS = 3.0
NO_RIGHTS_NOTICE_SECONDS = 6 * 3600.0


class WhitelistRepo:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._cache: TTLCache[tuple[int, int], frozenset[int]] = TTLCache(5_000, 600.0)

    async def ids(self, bot_id: int, chat_id: int) -> frozenset[int]:
        async def load() -> frozenset[int]:
            rows = await self._db.fetch(q.WHITELIST_IDS, bot_id, chat_id)
            return frozenset(row["user_id"] for row in rows)

        return await self._cache.get_or_load((bot_id, chat_id), load)

    async def add(self, bot_id: int, chat_id: int, user_id: int, by: int) -> bool:
        added = await self._db.fetchval(q.WHITELIST_ADD, bot_id, chat_id, user_id, by)
        self._cache.delete((bot_id, chat_id))
        return added is not None

    async def remove(self, bot_id: int, chat_id: int, user_id: int) -> bool:
        removed = await self._db.fetchval(q.WHITELIST_REMOVE, bot_id, chat_id, user_id)
        self._cache.delete((bot_id, chat_id))
        return removed is not None

    async def listing(self, bot_id: int, chat_id: int, limit: int) -> list[Any]:
        return await self._db.fetch(q.WHITELIST_LIST, bot_id, chat_id, limit)

    def forget(self, bot_id: int, chat_id: int) -> None:
        self._cache.delete((bot_id, chat_id))


class Messenger(Protocol):
    async def delete_many(self, chat_id: int, message_ids: tuple[int, ...]) -> None: ...
    async def send(
        self, chat_id: int, thread_id: int | None, text: str, undo_id: int | None = None
    ) -> int | None: ...
    async def delete(self, chat_id: int, message_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class Incoming:
    chat_id: int
    chat_username: str | None
    author: Person
    message_id: int
    thread_id: int | None
    album: str | None
    view: MessageView


class Protection:
    def __init__(
        self,
        moderation: Moderation,
        whitelist: WhitelistRepo,
        *,
        tracker: Tracker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._moderation = moderation
        self.whitelist = whitelist
        self.tracker = tracker or Tracker()
        self._clock = clock
        self._parsed: TTLCache[tuple[int, int], tuple[Any, ProtectionSettings]] = TTLCache(
            20_000, 3600.0
        )
        self._last_notice: TTLCache[tuple[int, int], float] = TTLCache(20_000, 60.0)
        self._told_no_rights: TTLCache[tuple[int, int], bool] = TTLCache(
            5_000, NO_RIGHTS_NOTICE_SECONDS
        )
        self._tasks: set[asyncio.Task[None]] = set()

    def settings_for(self, bot_id: int, chat_id: int, raw: dict[str, Any]) -> ProtectionSettings:
        # Настройки разбираются один раз, пока кэш чатов отдаёт тот же самый объект.
        key = (bot_id, chat_id)
        cached = self._parsed.get(key)
        if cached is not None and cached[0] is raw:
            return cached[1]
        parsed = ProtectionSettings.from_chat(raw)
        self._parsed.set(key, (raw, parsed))
        return parsed

    async def inspect(
        self, bot: Any, tg: Any, messenger: Messenger, raw_settings: dict[str, Any], item: Incoming
    ) -> bool:
        """True: сообщение убрано, дальше его обрабатывать не нужно."""
        settings = self.settings_for(bot.id, item.chat_id, raw_settings)
        hit = self.tracker.hit(
            (bot.id, item.chat_id, item.author.id),
            item.message_id,
            text=normalize(item.view.text) if settings.duplicate_limit else "",
            album=item.album,
            flood_limit=settings.flood_limit if settings.flood_enabled else 0,
            flood_window=settings.flood_window,
            duplicate_limit=settings.duplicate_limit,
            duplicate_window=settings.duplicate_window,
        )
        code = None
        if hit.flood:
            code = "flood"
        elif hit.duplicate:
            code = "duplicate"
        elif not hit.cooldown:
            code = find_violation(item.view, settings, item.chat_username)
        if code is None and not hit.cooldown:
            return False

        # Сюда попадает только нарушение, поэтому проверка прав не замедляет обычные сообщения.
        try:
            if await self._is_exempt(bot, item):
                return False
            rights = await self._moderation.admins.get(bot, item.chat_id, bot.id)
        except Exception:
            log.exception("Защита: не удалось проверить права в чате %s", item.chat_id)
            return False  # при сбое лучше пропустить сообщение, чем наказать зря
        if rights is None or not rights.can_delete:
            await self._tell_no_rights(bot.id, messenger, item)
            return False

        ids = hit.message_ids or (item.message_id,)
        await messenger.delete_many(item.chat_id, ids)
        if hit.cooldown:
            return True
        try:
            if code == "flood":
                await self._punish_flood(bot, tg, messenger, settings, item)
            else:
                await self._punish_spam(bot, tg, messenger, settings, raw_settings, item, code)
        except ModerationError as error:
            log.warning("Защита: чат %s, действие не выполнено: %s", item.chat_id, error.code)
            if error.code == "bot_no_rights":
                self._moderation.admins.invalidate(bot.id, item.chat_id)
                await self._tell_no_rights(bot.id, messenger, item)
        return True

    async def _is_exempt(self, bot: Any, item: Incoming) -> bool:
        if await self._moderation.admins.get(bot, item.chat_id, item.author.id) is not None:
            return True
        return item.author.id in await self.whitelist.ids(bot.id, item.chat_id)

    def _may_notify(self, bot_id: int, chat_id: int) -> bool:
        key = (bot_id, chat_id)
        now = self._clock()
        last = self._last_notice.get(key)
        if last is not None and now - last < NOTICE_GAP_SECONDS:
            return False
        self._last_notice.set(key, now)
        return True

    async def _tell_no_rights(self, bot_id: int, messenger: Messenger, item: Incoming) -> None:
        key = (bot_id, item.chat_id)
        if self._told_no_rights.get(key):
            return
        self._told_no_rights.set(key, True)
        await messenger.send(item.chat_id, item.thread_id, t("protect_no_rights"))

    async def _punish_flood(
        self, bot: Any, tg: Any, messenger: Messenger, settings: ProtectionSettings, item: Incoming
    ) -> None:
        await self._moderation.check_bot(bot, item.chat_id)
        outcome = await self._moderation.punish(
            tg,
            item.chat_id,
            settings.flood_action,
            item.author,
            AUTO_ADMIN,
            seconds=settings.flood_seconds if settings.flood_action != "kick" else None,
            reason=t("reason_flood"),
            source="flood",
        )
        if self._may_notify(bot.id, item.chat_id):
            await messenger.send(
                item.chat_id, item.thread_id, render_card(outcome, ModSettings().utc_offset_hours),
                undo_target(outcome),
            )

    async def _punish_spam(
        self,
        bot: Any,
        tg: Any,
        messenger: Messenger,
        settings: ProtectionSettings,
        raw_settings: dict[str, Any],
        item: Incoming,
        code: str,
    ) -> None:
        reason = t(f"reason_{code}")
        if settings.spam_action == "warn":
            mod_settings = ModSettings.from_chat(raw_settings)
            outcome = await self._moderation.warn(
                tg, item.chat_id, item.author, AUTO_ADMIN, mod_settings, reason=reason
            )
            if self._may_notify(bot.id, item.chat_id):
                await messenger.send(
                    item.chat_id, item.thread_id,
                    render_card(outcome, mod_settings.utc_offset_hours), undo_target(outcome),
                )
            return
        if self._may_notify(bot.id, item.chat_id):
            sent = await messenger.send(
                item.chat_id, item.thread_id,
                t("spam_notice", target=mention(item.author), reason=reason),
            )
            if sent is not None:
                self._delete_later(messenger, item.chat_id, sent, settings.notice_seconds)

    def _delete_later(
        self, messenger: Messenger, chat_id: int, message_id: int, seconds: int
    ) -> None:
        async def run() -> None:
            await asyncio.sleep(seconds)
            await messenger.delete(chat_id, message_id)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
