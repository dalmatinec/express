"""Запасные токены основного бота.

Заранее готовятся несколько ботов (до 5). Пока основной жив, они просто лежат. Основной заблокировали или
токен отозвали: автоматически берётся следующий запасной, и бот продолжает работать без участия человека.
Запасной бот должен быть админом во всех подключённых каналах с правом приглашать: при добавлении это проверяется.
Токены хранятся зашифрованными (SECRET_KEY)."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.types import ChatMemberAdministrator
from aiogram.utils.token import TokenValidationError, validate_token

from .app import App
from .mirrors import MirrorError, token_bot_id
from .store import now

log = logging.getLogger(__name__)

DEFAULT_MAX_RESERVES = 5


def env_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


@dataclass(slots=True)
class Reserve:
    bot_id: int
    username: str
    position: int
    status: str


class ReserveStore:
    def __init__(self, app: App, factory: Callable[[str], Bot]) -> None:
        self.app = app
        self.factory = factory

    @property
    def enabled(self) -> bool:
        return self.app.cipher is not None

    async def has(self, bot_id: int) -> bool:
        return bool(await self.app.db.fetchval("SELECT 1 FROM reserve_tokens WHERE bot_id = ?", (bot_id,)))

    async def listing(self) -> list[Reserve]:
        rows = await self.app.db.fetchall(
            "SELECT bot_id, username, position, status FROM reserve_tokens ORDER BY position")
        return [Reserve(r["bot_id"], r["username"] or str(r["bot_id"]), r["position"], r["status"]) for r in rows]

    async def add(self, token: str) -> tuple[Reserve, list[str]]:
        """Возвращает запасной и список замечаний (например, «не админ в канале X»)."""
        app = self.app
        if not self.enabled:
            raise MirrorError("Не задан SECRET_KEY: без него токены хранить нельзя.")
        token = token.strip()
        try:
            validate_token(token)
        except TokenValidationError as e:
            raise MirrorError("Это не похоже на токен бота.") from e
        bot_id = token_bot_id(token)
        if bot_id == app.main_bot.id:
            raise MirrorError("Это токен текущего основного бота.")
        if await self.has(bot_id):
            raise MirrorError("Этот запасной бот уже добавлен.")
        if await app.db.fetchval("SELECT 1 FROM mirrors WHERE bot_id = ?", (bot_id,)):
            raise MirrorError("Этот бот уже работает как зеркало, нужен отдельный.")
        count = await app.db.fetchval("SELECT COUNT(*) FROM reserve_tokens WHERE status != 'dead'")
        if count >= int(app.store.setting("reserves_max", DEFAULT_MAX_RESERVES)):
            raise MirrorError("Запасных ботов уже максимум, удалите ненужного.")
        bot = self.factory(token)
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError as e:
            raise MirrorError("Telegram не принял токен.") from e
        except TelegramAPIError as e:
            raise MirrorError("Не получилось проверить токен, попробуйте ещё раз.") from e
        position = (await app.db.fetchval("SELECT COALESCE(MAX(position), 0) FROM reserve_tokens")) + 1
        await app.db.execute(
            "INSERT INTO reserve_tokens(bot_id, username, token_enc, position, status, created_at) "
            "VALUES (?, ?, ?, ?, 'ready', ?)",
            (bot_id, me.username or str(me.id), app.cipher.encrypt(token), position, now()))  # type: ignore[union-attr]
        problems = await self.check_rights(bot)
        return Reserve(bot_id, me.username or str(me.id), position, "ready"), problems

    async def check_rights(self, bot: Bot) -> list[str]:
        """Запасной бот должен быть админом с правом приглашать во всех каналах основного."""
        problems: list[str] = []
        for chat in list(self.app.store.chats.values()):
            title = chat.title or str(chat.id)
            try:
                me = await bot.get_chat_member(chat.id, bot.id)
            except TelegramAPIError:
                problems.append(f"{title}: бота там нет")
                continue
            if me.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                problems.append(f"{title}: бота там нет")
            elif not (isinstance(me, ChatMemberAdministrator) and me.can_invite_users):
                problems.append(f"{title}: нет права «Приглашать пользователей»")
        return problems

    async def remove(self, bot_id: int) -> bool:
        return bool(await self.app.db.execute_rowcount(
            "DELETE FROM reserve_tokens WHERE bot_id = ? AND status != 'active'", (bot_id,)))

    async def candidates(self) -> list[tuple[int, str]]:
        """(bot_id, токен) готовых запасных по порядку."""
        out = []
        for r in await self.app.db.fetchall(
                "SELECT bot_id, token_enc FROM reserve_tokens WHERE status = 'ready' ORDER BY position"):
            try:
                out.append((r["bot_id"], self.app.cipher.decrypt(r["token_enc"])))  # type: ignore[union-attr]
            except Exception:
                log.error("Не удалось расшифровать запасной токен %s", r["bot_id"])
        return out

    async def mark(self, bot_id: int, status: str) -> None:
        await self.app.db.execute("UPDATE reserve_tokens SET status = ? WHERE bot_id = ?", (status, bot_id))

    async def activate(self, bot_id: int) -> None:
        """Запасной стал основным. Запоминаем выбор: после перезапуска берётся он, а не токен из .env."""
        await self.app.db.execute("UPDATE reserve_tokens SET status = 'dead' WHERE status = 'active'")
        await self.mark(bot_id, "active")
        await self.app.set_setting("main_override", {"bot_id": bot_id, "env": env_hash(self.app.config.bot_token)})
