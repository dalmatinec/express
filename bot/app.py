"""Общий контейнер сервисов и буферы записи (чтобы не дёргать базу на каждый клик)."""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from .config import Config
from .db import Database
from .media import MediaStore
from .store import Store, now

if TYPE_CHECKING:
    from .crypto import TokenCipher
    from .mirrors import MirrorManager
    from .notify import MirrorNotifier
    from .reserves import ReserveStore
    from .runtime import MainRunner
    from .services.ads import AdService
    from .services.broadcast import Broadcaster
    from .services.updates import UpdateNotifier
    from .services.links import LinkService

log = logging.getLogger(__name__)

BIND_WAIT = 10 * 60

# Бот, через которого сейчас отвечаем пользователю. Пусто - основной бот.
# Зеркало выставляет его на время обработки одного обновления: весь код, который шлёт сообщения
# через app.bot, автоматически отвечает именно из того бота, куда человек написал.
_current_bot: ContextVar[Bot | None] = ContextVar("current_bot", default=None)


@contextmanager
def use_bot(bot: Bot) -> Iterator[None]:
    token = _current_bot.set(bot)
    try:
        yield
    finally:
        _current_bot.reset(token)


class BotScoped(dict):
    """Словарь с раздельными ключами для основного бота и каждого зеркала.

    Номера сообщений у разных ботов относятся к разным чатам, поэтому один и тот же ключ
    (пользователь) не должен делиться между ними: иначе удалим чужое сообщение.
    Снаружи выглядит как обычный dict."""

    @staticmethod
    def _k(key: Any) -> Any:
        bot = _current_bot.get()
        return key if bot is None else (bot.id, key)

    def __getitem__(self, key: Any) -> Any:
        return super().__getitem__(self._k(key))

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(self._k(key), value)

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(self._k(key))

    def __contains__(self, key: object) -> bool:
        return super().__contains__(self._k(key))

    def get(self, key: Any, default: Any = None) -> Any:
        return super().get(self._k(key), default)

    def pop(self, key: Any, *default: Any) -> Any:
        return super().pop(self._k(key), *default)


@dataclass(slots=True)
class Screen:
    """Где у пользователя висит текущий экран бота."""
    message_id: int
    has_media: bool


@dataclass(slots=True)
class PendingBind:
    """Админ нажал Привязать чат и сейчас добавит бота в канал/группу."""
    kind: str      # item
    ref: int       # id кнопки
    until: int


@dataclass
class App:
    config: Config
    db: Database
    store: Store
    media: MediaStore
    main_bot: Bot
    bot_username: str = ""
    links: "LinkService" = None  # type: ignore[assignment]
    ads: "AdService" = None  # type: ignore[assignment]
    broadcaster: "Broadcaster" = None  # type: ignore[assignment]
    updates: "UpdateNotifier" = None  # type: ignore[assignment]
    cipher: "TokenCipher | None" = None
    reserves: "ReserveStore" = None  # type: ignore[assignment]
    mirrors: "MirrorManager" = None  # type: ignore[assignment]
    notifier: "MirrorNotifier" = None  # type: ignore[assignment]
    runner: "MainRunner | None" = None
    known_users: set[int] = field(default_factory=set)
    banned: dict[int, int | None] = field(default_factory=dict)  # user_id -> until (None = навсегда)
    screens: dict[int, Screen] = field(default_factory=BotScoped)
    pending_binds: dict[int, PendingBind] = field(default_factory=dict)
    link_msgs: dict[tuple[int, int], tuple[int, str]] = field(default_factory=BotScoped)  # (юзер, кнопка) -> (сообщение, ссылка)
    _seen: dict[int, tuple[int, str | None, str | None]] = field(default_factory=dict)
    _screen_ids: dict[int, int] = field(default_factory=dict)
    backup_due: int = 0  # когда пора сделать лёгкий бэкап после важных изменений (0 - не нужно)
    _seen_bots: dict[tuple[int, int], int] = field(default_factory=dict)  # (юзер, бот) -> когда видели
    _known_pairs: set[tuple[int, int]] = field(default_factory=set)

    @property
    def bot(self) -> Bot:
        """Бот для ответа пользователю: зеркало, из которого пришло обновление, иначе основной."""
        return _current_bot.get() or self.main_bot

    async def load_users(self) -> None:
        self.known_users = {r["id"] for r in await self.db.fetchall("SELECT id FROM users")}
        self.banned = {r["id"]: r["banned_until"]
                       for r in await self.db.fetchall("SELECT id, banned_until FROM users WHERE is_banned = 1")}

    async def reload(self) -> None:
        """Перечитать кэш. Сначала сбрасываем буферы - иначе живые счётчики затрутся старыми из базы."""
        await self.flush()
        await self.store.reload()

    def backup_soon(self) -> None:
        """Изменили зеркала или запасные токены: через несколько минут уйдёт лёгкий бэкап (только база),
        чтобы при потере сервера не ждать суточного."""
        if not self.backup_due:
            self.backup_due = now() + 300

    async def set_setting(self, key: str, value: Any) -> None:
        await self.db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.store.settings[key] = value

    # ---------- пользователи ----------
    async def touch_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        ts = now()
        if user_id not in self.known_users:
            self.known_users.add(user_id)
            await self.db.execute(
                "INSERT OR IGNORE INTO users(id, username, first_name, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, first_name, ts, ts),
            )
            return
        self._seen[user_id] = (ts, username, first_name)

    async def touch_user_bot(self, user_id: int, bot_id: int) -> None:
        """Запоминает, через какого бота пишет человек: зеркалам и запасным понадобится его достать."""
        pair = (user_id, bot_id)
        ts = now()
        if pair not in self._known_pairs:
            if len(self._known_pairs) > 1_000_000:  # повторная вставка безвредна (INSERT OR IGNORE)
                self._known_pairs.clear()
            self._known_pairs.add(pair)
            await self.db.execute(
                "INSERT OR IGNORE INTO user_bots(user_id, bot_id, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                (user_id, bot_id, ts, ts))
        self._seen_bots[pair] = ts

    async def mark_blocked(self, user_id: int, blocked: bool = True) -> None:
        if self._is_main_context():  # заблокировали зеркало - основным ботом человек по-прежнему доступен
            await self.db.execute("UPDATE users SET is_blocked = ? WHERE id = ?", (int(blocked), user_id))
        bot_id = self.bot.id
        await self.db.execute("UPDATE user_bots SET is_blocked = ? WHERE user_id = ? AND bot_id = ?",
                              (int(blocked), user_id, bot_id))

    def is_banned(self, user_id: int) -> bool:
        if user_id not in self.banned:
            return False
        until = self.banned[user_id]
        return until is None or until > now()

    async def ban(self, user_id: int, until: int | None, reason: str) -> None:
        if user_id not in self.known_users:
            self.known_users.add(user_id)
            await self.db.execute("INSERT OR IGNORE INTO users(id, created_at, last_seen) VALUES (?, ?, ?)",
                                  (user_id, now(), now()))
        self.banned[user_id] = until
        await self.db.execute("UPDATE users SET is_banned = 1, banned_until = ?, ban_reason = ? WHERE id = ?",
                              (until, reason, user_id))

    async def unban(self, user_id: int) -> None:
        self.banned.pop(user_id, None)
        await self.db.execute(
            "UPDATE users SET is_banned = 0, banned_until = NULL, ban_reason = NULL WHERE id = ?", (user_id,))

    # ---------- экраны ----------
    def set_screen(self, user_id: int, message_id: int, has_media: bool) -> None:
        if len(self.screens) > 300_000:  # память под контролем: после сброса экран просто пришлётся заново
            self.screens.clear()
        self.screens[user_id] = Screen(message_id, has_media)
        if self._is_main_context():  # в базе номер экрана хранится только для основного бота
            self._screen_ids[user_id] = message_id

    def _is_main_context(self) -> bool:
        current = _current_bot.get()
        return current is None or current is self.main_bot

    async def last_screen_id(self, user_id: int) -> int | None:
        """Id прошлого экрана - из памяти или (после перезапуска) из базы."""
        if user_id in self.screens:
            return self.screens[user_id].message_id
        if not self._is_main_context():
            return None
        return await self.db.fetchval("SELECT screen_msg_id FROM users WHERE id = ?", (user_id,))

    def remember_link_msg(self, key: tuple[int, int], message_id: int, link: str) -> None:
        if len(self.link_msgs) > 300_000:  # память под контролем
            self.link_msgs.clear()
        self.link_msgs[key] = (message_id, link)

    # ---------- привязка чатов ----------
    def start_bind(self, admin_id: int, kind: str, ref: int = 0) -> None:
        self.pending_binds[admin_id] = PendingBind(kind, ref, now() + BIND_WAIT)

    def pending_bind(self, admin_id: int) -> PendingBind | None:
        p = self.pending_binds.get(admin_id)
        if p is None or p.until < now():
            self.pending_binds.pop(admin_id, None)
            return None
        return p

    # ---------- буферы ----------
    async def flush(self) -> None:
        """Сбрасывает накопленные записи в базу одной пачкой."""
        seen, self._seen = self._seen, {}
        screen_ids, self._screen_ids = self._screen_ids, {}
        if seen:
            await self.db.executemany(
                "UPDATE users SET last_seen = ?, username = ?, first_name = ?, is_blocked = 0 WHERE id = ?",
                ((ts, un, fn, uid) for uid, (ts, un, fn) in seen.items()),
            )
        if screen_ids:
            await self.db.executemany("UPDATE users SET screen_msg_id = ? WHERE id = ?",
                                      ((mid, uid) for uid, mid in screen_ids.items()))
        seen_bots, self._seen_bots = self._seen_bots, {}
        if seen_bots:
            await self.db.executemany(
                "UPDATE user_bots SET last_seen = ?, is_blocked = 0 WHERE user_id = ? AND bot_id = ?",
                ((ts, uid, bid) for (uid, bid), ts in seen_bots.items()))
        if self.ads is not None:
            await self.ads.flush()

    # ---------- админы ----------
    def admin_ids(self, perm: str | None = None) -> list[int]:
        ids = set(self.config.owner_ids)
        for uid, perms in self.store.admins.items():
            if perm is None or perm in perms:
                ids.add(uid)
        return sorted(ids)

    def perms(self, user_id: int) -> set[str] | None:
        return self.store.perms_of(user_id, self.config.owner_ids)

    def has_perm(self, user_id: int, perm: str) -> bool:
        perms = self.perms(user_id)
        return perms is not None and ("*" in perms or perm in perms)

    async def notify_admins(self, html: str, perm: str | None = None, **kwargs) -> None:
        for uid in self.admin_ids(perm):
            try:
                await self.main_bot.send_message(uid, html, **kwargs)
            except TelegramForbiddenError:
                pass
            except TelegramAPIError as e:
                log.warning("Не удалось уведомить админа %s: %s", uid, e)

    async def alert(self, html: str, perm: str | None = None, **kwargs) -> None:
        """Важное событие: админам в личку и в канал логов."""
        await self.notify_admins(html, perm, **kwargs)
        await self.log_event(html)

    async def log_action(self, admin_id: int, action: str, details: str = "", pretty: str = "") -> None:
        await self.db.execute("INSERT INTO admin_log(admin_id, action, details, ts) VALUES (?, ?, ?, ?)",
                              (admin_id, action, details[:500], now()))
        if pretty and self.store.setting("log_admin_actions", 1):
            await self.log_event(f"📜 {pretty}")

    # ---------- канал логов ----------
    @property
    def log_chat(self) -> int:
        return int(self.store.setting("log_chat_id", 0) or 0)

    async def log_event(self, html: str, **kwargs) -> bool:
        chat = self.log_chat
        if not chat:
            return False
        try:
            await self.main_bot.send_message(chat, html[:4000], disable_notification=True, **kwargs)
            return True
        except TelegramAPIError as e:
            log.warning("Канал логов недоступен: %s", e)
            return False
