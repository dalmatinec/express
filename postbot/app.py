"""Общий контейнер: конфиг, база, бот, настройки, кто админ, какие экраны сейчас открыты."""
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .config import Config
from .db import Database
from .models import Admin, Channel, from_row

if TYPE_CHECKING:
    from .services.media import MediaStore

log = logging.getLogger(__name__)


def now() -> int:
    return int(time.time())


@dataclass
class App:
    config: Config
    db: Database
    bot: Bot
    bot_id: int = 0
    bot_username: str = ""
    media: "MediaStore" = None  # type: ignore[assignment]
    settings: dict[str, Any] = field(default_factory=dict)
    admins: dict[int, Admin] = field(default_factory=dict)
    # экраны бота у каждого юзера: panel - панель с кнопками, preview - как выглядит пост
    screens: dict[int, dict[str, int]] = field(default_factory=dict)
    # кто сейчас выбирает канал через кнопку, чтобы не писать ему дважды
    connecting: set[int] = field(default_factory=set)

    async def load(self) -> None:
        self.settings = {r["key"]: json.loads(r["value"]) for r in await self.db.fetchall("SELECT * FROM settings")}
        self.admins = {r["user_id"]: from_row(Admin, r) for r in await self.db.fetchall("SELECT * FROM admins")}

    # ---------- настройки ----------
    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    async def set_setting(self, key: str, value: Any) -> None:
        await self.db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                              (key, json.dumps(value, ensure_ascii=False)))
        self.settings[key] = value

    # ---------- доступ ----------
    def is_owner(self, user_id: int) -> bool:
        return user_id in self.config.owner_ids

    def role(self, user_id: int) -> str | None:
        if self.is_owner(user_id):
            return "owner"
        if user_id in self.admins:
            return "admin"
        return None

    def can_manage_channels(self, user_id: int) -> bool:
        if self.is_owner(user_id):
            return True
        admin = self.admins.get(user_id)
        return bool(admin and admin.can_channels)

    async def all_channels(self) -> list[Channel]:
        rows = await self.db.fetchall("SELECT * FROM channels ORDER BY title COLLATE NOCASE")
        return [from_row(Channel, r) for r in rows]

    async def channel(self, channel_id: int) -> Channel | None:
        row = await self.db.fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
        return from_row(Channel, row) if row else None

    async def channels_for(self, user_id: int) -> list[Channel]:
        """Каналы, куда этому человеку можно постить."""
        channels = await self.all_channels()
        admin = self.admins.get(user_id)
        if self.is_owner(user_id) or (admin and admin.all_channels):
            return channels
        if admin is None:
            return []
        allowed = {r[0] for r in await self.db.fetchall(
            "SELECT channel_id FROM admin_channels WHERE user_id = ?", (user_id,))}
        return [c for c in channels if c.id in allowed]

    # ---------- время ----------
    def local(self, ts: int) -> datetime:
        return datetime.fromtimestamp(ts, self.config.tz)

    def fmt(self, ts: int | None, short: bool = False) -> str:
        if not ts:
            return "-"
        dt = self.local(ts)
        if short:
            return dt.strftime("%d.%m %H:%M")
        return dt.strftime("%d.%m.%Y %H:%M")

    # ---------- уведомления ----------
    async def notify(self, user_id: int, text: str, **kwargs) -> int | None:
        try:
            msg = await self.bot.send_message(user_id, text, **kwargs)
            return msg.message_id
        except TelegramAPIError as e:
            log.warning("Не смог написать %s: %s", user_id, e)
            return None

    async def notify_owners(self, text: str, **kwargs) -> None:
        for uid in sorted(self.config.owner_ids):
            await self.notify(uid, text, **kwargs)
