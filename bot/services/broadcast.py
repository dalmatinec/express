"""Рассылка в фоне: ~20 сообщений в секунду, прогресс виден в админке, можно остановить."""
import asyncio
import logging
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message

from ..app import App, use_bot
from ..store import now

log = logging.getLogger(__name__)

AUDIENCES = {
    "all": ("👥 Всем", "is_blocked = 0 AND is_banned = 0"),
    "active7": ("🔥 Активным за 7 дней", "is_blocked = 0 AND is_banned = 0 AND last_seen > ?"),
    "active30": ("📅 Активным за 30 дней", "is_blocked = 0 AND is_banned = 0 AND last_seen > ?"),
}


@dataclass
class Snapshot:
    """Содержимое поста для зеркал: пересылать и копировать из чата админа с основным ботом зеркало не может,
    поэтому текст, разметка и файл запоминаются, и зеркало отправляет пост от себя."""
    kind: str  # text | photo | animation | video | document
    html: str
    file_id: str | None = None
    markup: InlineKeyboardMarkup | None = None

    @classmethod
    def from_message(cls, m: Message) -> "Snapshot":
        if m.photo:
            kind, fid = "photo", m.photo[-1].file_id
        elif m.animation:
            kind, fid = "animation", m.animation.file_id
        elif m.video:
            kind, fid = "video", m.video.file_id
        elif m.document:
            kind, fid = "document", m.document.file_id
        else:
            kind, fid = "text", None
        return cls(kind, m.html_text or "", fid, m.reply_markup)


class MirrorSender:
    """Отправка снимка через зеркала: файл скачивается один раз, в каждое зеркало заливается один раз."""

    def __init__(self, main_bot: Bot, snap: Snapshot) -> None:
        self.main_bot, self.snap = main_bot, snap
        self._data: bytes | None = None
        self._ids: dict[int, str] = {}

    async def _source(self, bot: Bot):
        if bot.id in self._ids:
            return self._ids[bot.id]
        if self._data is None:
            buf = await self.main_bot.download(self.snap.file_id)
            assert buf is not None
            self._data = buf.getvalue()
        return BufferedInputFile(self._data, filename="file")

    async def send(self, bot: Bot, uid: int, kb: InlineKeyboardMarkup | None) -> None:
        snap = self.snap
        markup = kb or snap.markup
        if snap.kind == "text":
            await bot.send_message(uid, snap.html, reply_markup=markup)
            return
        src = await self._source(bot)
        caption = snap.html or None
        if snap.kind == "photo":
            msg = await bot.send_photo(uid, src, caption=caption, reply_markup=markup)
            fid = msg.photo[-1].file_id if msg.photo else None
        elif snap.kind == "animation":
            msg = await bot.send_animation(uid, src, caption=caption, reply_markup=markup)
            fid = msg.animation.file_id if msg.animation else None
        elif snap.kind == "video":
            msg = await bot.send_video(uid, src, caption=caption, reply_markup=markup)
            fid = msg.video.file_id if msg.video else None
        else:
            msg = await bot.send_document(uid, src, caption=caption, reply_markup=markup)
            fid = msg.document.file_id if msg.document else None
        if fid:
            self._ids[bot.id] = fid


@dataclass
class Broadcast:
    admin_id: int
    total: int
    started: int = field(default_factory=now)
    sent: int = 0
    blocked: int = 0
    failed: int = 0
    via_mirrors: int = 0
    cancelled: bool = False
    finished: int | None = None

    @property
    def done(self) -> int:
        return self.sent + self.blocked + self.failed

    def summary(self) -> str:
        mir = f" (через зеркала: {self.via_mirrors})" if self.via_mirrors else ""
        return (f"✅ Доставлено: <b>{self.sent}</b>{mir}\n🚫 Заблокировали бота: <b>{self.blocked}</b>\n"
                f"⚠️ Ошибки: <b>{self.failed}</b>\nВсего: {self.done} из {self.total}")


class Broadcaster:
    def __init__(self, app: App) -> None:
        self.app = app
        self.current: Broadcast | None = None
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def audience(self, key: str) -> list[int]:
        """Кому пишет основной бот: те, кто его запускал (или старые записи без учёта по ботам)."""
        where = AUDIENCES.get(key, AUDIENCES["all"])[1]
        days = {"active7": 7, "active30": 30}.get(key)
        params = (now() - days * 86400,) if days else ()
        rows = await self.app.db.fetchall(
            f"SELECT id FROM users WHERE {where} AND (EXISTS (SELECT 1 FROM user_bots WHERE user_id = users.id "
            f"AND bot_id = ?) OR NOT EXISTS (SELECT 1 FROM user_bots WHERE user_id = users.id))",
            (*params, self.app.main_bot.id))
        return [r["id"] for r in rows]

    def _activity(self, key: str) -> tuple[str, tuple]:
        days = {"active7": 7, "active30": 30}.get(key)
        return ("AND ub.last_seen > ?", (now() - days * 86400,)) if days else ("", ())

    async def mirror_audience(self, key: str) -> list[tuple[int, int]]:
        """Люди, до которых можно дотянуться только через зеркало: (user_id, bot_id). Один человек - один раз."""
        mirrors = self.app.mirrors
        if mirrors is None or not mirrors.enabled:
            return []
        live = [m for m in mirrors.running_ids()]
        if not live:
            return []
        main_ids = set(await self.audience(key))
        extra, params = self._activity(key)
        marks = ",".join("?" * len(live))
        rows = await self.app.db.fetchall(
            f"SELECT ub.user_id, ub.bot_id FROM user_bots ub JOIN users u ON u.id = ub.user_id "
            f"WHERE ub.bot_id IN ({marks}) AND ub.is_blocked = 0 AND u.is_banned = 0 {extra} "
            f"ORDER BY ub.last_seen DESC", (*live, *params))
        seen: set[int] = set()
        out = []
        for r in rows:
            uid = r["user_id"]
            if uid not in main_ids and uid not in seen:
                seen.add(uid)
                out.append((uid, r["bot_id"]))
        return out

    async def count(self, key: str) -> int:
        return len(await self.audience(key)) + len(await self.mirror_audience(key))

    def start(self, admin_id: int, from_chat: int, message_id: int, ids: list[int],
              kb: InlineKeyboardMarkup | None, forward: bool = False,
              snap: Snapshot | None = None, mirror_ids: list[tuple[int, int]] | None = None) -> None:
        """Рассылка копией сообщения админа или настоящей пересылкой (с подписью «Переслано из...»).
        Людям из зеркал пост уходит от зеркала по снимку (там пересылка невозможна, шлётся копия)."""
        async def send(uid: int) -> None:
            if forward:
                await self.app.bot.forward_message(uid, from_chat, message_id)
            else:
                await self.app.bot.copy_message(uid, from_chat, message_id, reply_markup=kb)
        sender = MirrorSender(self.app.main_bot, snap) if snap is not None and mirror_ids else None

        async def send_mirror(uid: int, bot: Bot) -> None:
            assert sender is not None
            await sender.send(bot, uid, kb)
        self._launch(admin_id, ids, send, "Рассылка", mirror_ids if sender else None, send_mirror)

    def start_text(self, admin_id: int, html: str, ids: list[int], title: str = "Рассылка") -> None:
        """Рассылка готового текста (например, уведомление об обновлении ссылок)."""
        async def send(uid: int) -> None:
            await self.app.bot.send_message(uid, html)
        self._launch(admin_id, ids, send, title)

    def _launch(self, admin_id: int, ids: list[int], send, title: str,
                mirror_ids: list[tuple[int, int]] | None = None, send_mirror=None) -> None:
        self.current = Broadcast(admin_id, len(ids) + len(mirror_ids or []))
        self.task = asyncio.create_task(self._run(self.current, ids, send, title, mirror_ids or [], send_mirror))

    async def _deliver(self, b: Broadcast, uid: int, send, mirror: bool = False) -> None:
        while True:
            try:
                await send(uid)
                b.sent += 1
                b.via_mirrors += mirror
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                continue
            except TelegramForbiddenError:
                b.blocked += 1
                await self.app.mark_blocked(uid)
            except Exception as e:  # noqa: BLE001 - одна ошибка не должна ронять всю рассылку
                b.failed += 1
                if not isinstance(e, TelegramAPIError):
                    log.exception("Рассылка: сбой отправки %s", uid)
            return

    async def _run(self, b: Broadcast, ids: list[int], send, title: str,
                   mirror_ids: list[tuple[int, int]], send_mirror) -> None:
        bot = self.app.bot
        for uid in ids:
            if b.cancelled:
                break
            await self._deliver(b, uid, send)
            await asyncio.sleep(0.05)
        mirrors = self.app.mirrors
        for uid, bot_id in mirror_ids:
            if b.cancelled:
                break
            mbot = mirrors.bot_of(bot_id) if mirrors is not None else None
            if mbot is None:
                b.failed += 1
                continue
            with use_bot(mbot):  # блокировку отметим именно за зеркалом
                await self._deliver(b, uid, lambda u, mb=mbot: send_mirror(u, mb), mirror=True)
            await asyncio.sleep(0.05)
        b.finished = now()
        report = f"📣 <b>{title} {'остановлена' if b.cancelled else 'завершена'}</b>\n" + b.summary()
        try:
            await bot.send_message(b.admin_id, report)
        except TelegramAPIError:
            log.warning("Не удалось отправить отчёт о рассылке")
        await self.app.log_event(report)
