"""Логика модерации. Без aiogram: Telegram подключается через объект с действиями."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..admins import AdminCache, AdminRights

log = logging.getLogger(__name__)

SYSTEM_ADMIN = 0


class ModerationError(Exception):
    """code соответствует тексту mod_err_<code>."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def classify_telegram_error(message: str) -> str:
    """Telegram различает причины только текстом ошибки."""
    text = message.lower()
    if "not enough rights" in text or "chat_admin_required" in text or "have no rights" in text:
        return "bot_no_rights"
    if "administrator" in text or "chat owner" in text or "user_is_an_admin" in text:
        return "target_admin"
    if "user_not_participant" in text or "not a member" in text:
        return "not_in_chat"
    if "user not found" in text or "participant_id_invalid" in text or "user_id_invalid" in text:
        return "user_not_found"
    if "can't restrict self" in text or "can't remove self" in text or "myself" in text:
        return "target_bot"
    return "tg_error"


class TelegramActions(Protocol):
    bot_id: int

    async def ban(self, chat_id: int, user_id: int, until: datetime | None) -> None: ...
    async def unban(self, chat_id: int, user_id: int) -> None: ...
    async def mute(self, chat_id: int, user_id: int, until: datetime | None) -> None: ...
    async def unmute(self, chat_id: int, user_id: int) -> None: ...
    async def kick(self, chat_id: int, user_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class Person:
    id: int
    name: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class ModSettings:
    warn_limit: int = 3
    warn_action: str = "mute"  # ban, mute, kick
    warn_action_seconds: int | None = 86_400  # None: навсегда
    warn_ttl_seconds: int | None = None  # None: не сгорают
    delete_commands: bool = True
    utc_offset_hours: int = 5

    @classmethod
    def from_chat(cls, settings: dict[str, Any] | None) -> ModSettings:
        data = (settings or {}).get("mod") or {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: str
    target: Person
    admin: Person
    reason: str | None = None
    seconds: int | None = None
    until: datetime | None = None
    punishment_id: int | None = None
    warn_count: int = 0
    warn_limit: int = 0
    escalated: Outcome | None = None


class Moderation:
    def __init__(
        self,
        repo: Any,
        admins: AdminCache,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.admins = admins
        self._now = now

    # --- Проверка прав ---

    async def admin_rights(self, bot: Any, chat_id: int, user_id: int) -> AdminRights | None:
        return await self.admins.get(bot, chat_id, user_id)

    async def check_target(self, bot: Any, chat_id: int, admin_id: int, target_id: int) -> None:
        if target_id == bot.id:
            raise ModerationError("target_bot")
        if target_id == admin_id:
            raise ModerationError("self")
        admins = await self.admins.get_all(bot, chat_id)
        if target_id in admins:
            raise ModerationError("target_admin")

    async def check_bot(self, bot: Any, chat_id: int) -> None:
        rights = await self.admins.get(bot, chat_id, bot.id)
        if rights is None or not rights.can_restrict:
            # Права могли выдать только что: при следующей попытке спросим Telegram заново.
            self.admins.invalidate(bot.id, chat_id)
            raise ModerationError("bot_no_rights")

    # --- Наказания ---

    def _until(self, seconds: int | None) -> datetime | None:
        return None if seconds is None else self._now() + timedelta(seconds=seconds)

    async def punish(
        self,
        tg: TelegramActions,
        chat_id: int,
        kind: str,
        target: Person,
        admin: Person,
        *,
        seconds: int | None = None,
        reason: str | None = None,
        source: str = "manual",
    ) -> Outcome:
        until = self._until(seconds)
        if kind == "ban":
            await tg.ban(chat_id, target.id, until)
        elif kind == "mute":
            await tg.mute(chat_id, target.id, until)
        elif kind == "kick":
            await tg.kick(chat_id, target.id)
            until, seconds = None, None
        else:
            raise ValueError(f"Неизвестное наказание: {kind}")

        punishment_id = await self._safe_record(
            tg.bot_id, chat_id, target.id, kind, admin.id, reason, until, source
        )
        return Outcome(kind, target, admin, reason, seconds, until, punishment_id)

    async def _safe_record(self, *args: Any) -> int | None:
        """Telegram уже выполнил действие: сбой журнала не должен выглядеть как отказ."""
        try:
            return await self.repo.record(*args)
        except Exception:
            log.exception("Не удалось записать наказание в журнал")
            return None

    async def warn(
        self,
        tg: TelegramActions,
        chat_id: int,
        target: Person,
        admin: Person,
        settings: ModSettings,
        *,
        reason: str | None = None,
    ) -> Outcome:
        expires = self._until(settings.warn_ttl_seconds)
        punishment_id = await self.repo.record(
            tg.bot_id, chat_id, target.id, "warn", admin.id, reason, expires
        )
        count = await self.repo.count_warns(tg.bot_id, chat_id, target.id)
        escalated = None
        if count >= settings.warn_limit:
            # Если Telegram откажет, предупреждения останутся и админ сможет повторить.
            escalated = await self.punish(
                tg,
                chat_id,
                settings.warn_action,
                target,
                admin,
                seconds=settings.warn_action_seconds,
                reason=reason,
                source="warn_limit",
            )
            await self.repo.revoke_active(tg.bot_id, chat_id, target.id, "warn", SYSTEM_ADMIN)
        return Outcome(
            "warn",
            target,
            admin,
            reason,
            until=expires,
            punishment_id=punishment_id,
            warn_count=count,
            warn_limit=settings.warn_limit,
            escalated=escalated,
        )

    # --- Снятие ---

    async def lift(
        self, tg: TelegramActions, chat_id: int, kind: str, target: Person, admin: Person
    ) -> Outcome:
        if kind == "ban":
            await tg.unban(chat_id, target.id)
        elif kind == "mute":
            await tg.unmute(chat_id, target.id)
        else:
            raise ValueError(f"Нельзя снять: {kind}")
        await self.repo.revoke_active(tg.bot_id, chat_id, target.id, kind, admin.id)
        return Outcome(kind, target, admin)

    async def unwarn(
        self, tg: TelegramActions, chat_id: int, target: Person, admin: Person
    ) -> Outcome:
        revoked = await self.repo.revoke_last_warn(tg.bot_id, chat_id, target.id, admin.id)
        if revoked is None:
            raise ModerationError("no_warns")
        return Outcome("warn", target, admin)

    async def undo(
        self, tg: TelegramActions, chat_id: int, punishment_id: int, admin: Person
    ) -> Outcome:
        """Кнопка «Снять» на карточке."""
        row = await self.repo.get(punishment_id, tg.bot_id, chat_id)
        if row is None:
            raise ModerationError("not_found")
        kind = row["kind"]
        if kind == "kick":
            raise ModerationError("kick_undo")
        expires = row["expires_at"]
        if row["revoked_at"] is not None or (expires is not None and expires <= self._now()):
            raise ModerationError("already")
        target = Person(
            row["user_id"], row["first_name"] or f"ID {row['user_id']}", row["username"]
        )
        if kind == "ban":
            await tg.unban(chat_id, target.id)
        elif kind == "mute":
            await tg.unmute(chat_id, target.id)
        if not await self.repo.revoke_by_id(punishment_id, admin.id):
            raise ModerationError("already")
        return Outcome(kind, target, admin)
