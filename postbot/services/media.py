"""Медиа постов. Файл скачиваем к себе в data/media, чтобы бэкап можно было перенести на другой токен:
у нового бота file_id ещё нет, тогда файл заливается с диска и новый file_id запоминается."""
import logging
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, Message

from ..app import App, now

log = logging.getLogger(__name__)

DOWNLOAD_LIMIT = 20 * 1024 * 1024  # больше Bot API ботам файлы не отдаёт
KIND_NAMES = {"photo": "фото", "video": "видео", "animation": "GIF", "document": "файл", "audio": "аудио"}
EXT = {"photo": ".jpg", "video": ".mp4", "animation": ".mp4", "audio": ".mp3", "document": ""}


@dataclass(slots=True)
class MediaInfo:
    id: int
    kind: str
    path: Path | None
    file_name: str | None
    size: int


def media_of(message: Message) -> tuple[str, str, str | None, int] | None:
    """-> (вид, file_id, имя файла, размер) или None, если в сообщении нет поддерживаемого медиа."""
    if message.photo:
        p = message.photo[-1]
        return "photo", p.file_id, None, p.file_size or 0
    if message.animation:  # у GIF Telegram дублирует document, поэтому проверяем раньше
        a = message.animation
        return "animation", a.file_id, a.file_name, a.file_size or 0
    if message.video:
        v = message.video
        return "video", v.file_id, v.file_name, v.file_size or 0
    if message.audio:
        a = message.audio
        return "audio", a.file_id, a.file_name, a.file_size or 0
    if message.document:
        d = message.document
        return "document", d.file_id, d.file_name, d.file_size or 0
    return None


class MediaStore:
    def __init__(self, app: App) -> None:
        self.app = app
        self.dir = app.config.media_dir

    async def get(self, media_id: int | None) -> MediaInfo | None:
        if not media_id:
            return None
        row = await self.app.db.fetchone("SELECT * FROM media WHERE id = ?", (media_id,))
        if row is None:
            return None
        path = self.dir / row["path"] if row["path"] else None
        return MediaInfo(row["id"], row["kind"], path, row["file_name"], row["size"])

    async def save(self, bot: Bot, kind: str, file_id: str, file_name: str | None, size: int) -> tuple[int, bool]:
        """Сохраняет медиа. -> (id, удалось ли скачать файл к себе)."""
        db = self.app.db
        media_id = await db.execute("INSERT INTO media(kind, file_name, size, created_at) VALUES (?, ?, ?, ?)",
                                    (kind, file_name, size, now()))
        await self.remember(media_id, file_id)
        saved = False
        if size <= DOWNLOAD_LIMIT:
            ext = Path(file_name).suffix if file_name and Path(file_name).suffix else EXT.get(kind, "")
            name = f"{media_id}{ext}"
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{name}.tmp"
            try:
                await bot.download(file_id, destination=tmp)
                tmp.replace(self.dir / name)
                await db.execute("UPDATE media SET path = ? WHERE id = ?", (name, media_id))
                saved = True
            except (TelegramAPIError, OSError) as e:
                log.warning("Не скачал медиа %s: %s", media_id, e)
                tmp.unlink(missing_ok=True)
        return media_id, saved

    async def remember(self, media_id: int, file_id: str) -> None:
        await self.app.db.execute(
            "INSERT OR REPLACE INTO media_file_ids(media_id, bot_id, file_id) VALUES (?, ?, ?)",
            (media_id, self.app.bot_id, file_id))

    async def file_id(self, media_id: int) -> str | None:
        return await self.app.db.fetchval(
            "SELECT file_id FROM media_file_ids WHERE media_id = ? AND bot_id = ?", (media_id, self.app.bot_id))

    async def source(self, media_id: int, force_disk: bool = False) -> str | FSInputFile | None:
        """Что отдать в Telegram: file_id этого бота или файл с диска."""
        if not force_disk:
            fid = await self.file_id(media_id)
            if fid:
                return fid
        info = await self.get(media_id)
        if info and info.path and info.path.exists():
            return FSInputFile(info.path, filename=info.file_name or info.path.name)
        return None

    async def forget_file_id(self, media_id: int) -> None:
        await self.app.db.execute("DELETE FROM media_file_ids WHERE media_id = ? AND bot_id = ?",
                                  (media_id, self.app.bot_id))

    async def cleanup(self) -> int:
        """Удаляет медиа, на которые больше не ссылается ни один пост."""
        # свежие не трогаем: их могли только что скачать под пост, который ещё не записан
        rows = await self.app.db.fetchall(
            "SELECT id, path FROM media WHERE created_at < ? "
            "AND id NOT IN (SELECT media_id FROM posts WHERE media_id IS NOT NULL)", (now() - 600,))
        for r in rows:
            if r["path"]:
                (self.dir / r["path"]).unlink(missing_ok=True)
        if rows:
            await self.app.db.executemany("DELETE FROM media WHERE id = ?", ((r["id"],) for r in rows))
        return len(rows)
