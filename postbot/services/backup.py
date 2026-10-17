"""Бэкап: база и медиа в один zip. На сервере всегда лежит один файл data/backup.zip,
владельцам в личку приходит свежий файл, а прошлый из чата удаляется."""
import asyncio
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

from ..app import App, now

SEND_LIMIT = 50 * 1024 * 1024  # больше бот в Telegram отправить не может


def _zip(db_copy: Path, media_dir: Path, target: Path) -> None:
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_copy, "bot.db")
        if media_dir.exists():
            for f in sorted(media_dir.iterdir()):
                if f.is_file() and not f.name.endswith(".tmp"):
                    zf.write(f, f"media/{f.name}", compress_type=zipfile.ZIP_STORED)  # медиа и так сжаты
    tmp.replace(target)  # старый бэкап заменяется только когда новый готов


async def make_backup(app: App) -> Path:
    target = app.config.backup_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "bot.db"
        await app.db.backup_to(db_copy)
        await asyncio.to_thread(_zip, db_copy, app.config.media_dir, target)
    await app.set_setting("last_backup", now())
    return target


async def send_backup(app: App, path: Path, reason: str) -> list[str]:
    """Шлёт файл всем владельцам, прошлый файл в их чате удаляет. -> список ошибок."""
    errors: list[str] = []
    size = path.stat().st_size
    if size > SEND_LIMIT:
        return [f"файл {size // 1024 // 1024} МБ, Telegram не даёт боту отправить больше 50 МБ. "
                f"Он лежит на сервере: {path}"]
    old: dict[str, int] = dict(app.setting("backup_msgs", {}))
    stamp = app.local(now()).strftime("%Y%m%d_%H%M")
    caption = (f"💾 Бэкап бота @{app.bot_username}, {app.fmt(now())}\n{reason}\n\n"
               "Чтобы восстановить: Админка, Бэкап, Восстановить, и пришлите этот файл.")
    for uid in sorted(app.config.owner_ids):
        try:
            msg = await app.bot.send_document(uid, FSInputFile(path, filename=f"backup_{stamp}.zip"),
                                              caption=caption, disable_notification=True)
        except TelegramAPIError as e:
            errors.append(f"{uid}: {e.message}")
            continue
        prev = old.get(str(uid))
        if prev:
            try:
                await app.bot.delete_message(uid, prev)
            except TelegramAPIError:
                pass  # уже удалили руками или старше 48 часов
        old[str(uid)] = msg.message_id
    await app.set_setting("backup_msgs", old)
    return errors


def _check_and_extract(zip_path: Path, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise ValueError("это не zip-архив")
    with zf:
        names = zf.namelist()
        if "bot.db" not in names:
            raise ValueError("в архиве нет bot.db, это не бэкап этого бота")
        for name in names:
            ok = name == "bot.db" or (name.startswith("media/") and "/" not in name[6:] and ".." not in name)
            if not ok:
                raise ValueError(f"лишний файл в архиве: {name}")
        zf.extractall(dest)
    conn = sqlite3.connect(dest / "bot.db")
    try:
        conn.execute("SELECT COUNT(*) FROM posts").fetchone()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("база в архиве повреждена")
    except sqlite3.DatabaseError as e:
        raise ValueError(f"это не база бота: {e}")
    finally:
        conn.close()


def restore_files(zip_path: Path, db_path: Path, media_dir: Path) -> None:
    """Подменяет базу и медиа файлами из архива. В это время бот не должен писать в базу."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _check_and_extract(zip_path, tmp_dir)
        if db_path.exists():
            shutil.copy2(db_path, db_path.with_name(f"bot.db.before_restore_{datetime.now():%Y%m%d_%H%M%S}"))
        for suffix in ("-wal", "-shm"):
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_dir / "bot.db", db_path)
        media_dir.mkdir(parents=True, exist_ok=True)
        src = tmp_dir / "media"
        if src.exists():
            for f in src.iterdir():
                shutil.copy2(f, media_dir / f.name)


async def restore_backup(app: App, zip_path: Path) -> None:
    await app.db.close()
    try:
        await asyncio.to_thread(restore_files, zip_path, app.config.db_path, app.config.media_dir)
    finally:
        await app.db.connect()  # если бэкап со старой версии, миграции догонят
        await app.load()
        app.screens.clear()
