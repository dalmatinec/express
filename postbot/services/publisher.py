"""Публикация в каналы, правка и удаление уже вышедших постов, отложенные и автоудаление."""
import asyncio
import logging
from dataclasses import dataclass
from html import escape

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from ..app import App, now
from ..models import Channel, Publication, from_row
from . import posts as P
from .render import RenderError, edit_post, send_post

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Result:
    channel_id: int
    title: str
    ok: bool
    info: str  # ссылка на пост или текст ошибки
    message_id: int | None = None


def human_error(e: Exception) -> str:
    text = str(e.message if hasattr(e, "message") else e)
    low = text.lower()
    if "not enough rights" in low or "need administrator rights" in low or "chat_admin_required" in low:
        return "у бота нет прав на это действие"
    if "chat not found" in low:
        return "канал не найден, бота там нет"
    if "bot was kicked" in low or "not a member" in low:
        return "бота убрали из канала"
    if "message to delete not found" in low or "message to edit not found" in low:
        return "сообщения уже нет"
    if "message can't be deleted" in low:
        return "Telegram не даёт удалить это сообщение"
    if "message can't be edited" in low:
        return "Telegram не даёт изменить это сообщение"
    return text.replace("Bad Request: ", "").replace("Forbidden: ", "")


async def _call(coro_factory, tries: int = 3):
    """Повтор, если Telegram попросил подождать."""
    for attempt in range(tries):
        try:
            return await coro_factory()
        except TelegramRetryAfter as e:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(min(e.retry_after, 60) + 1)


async def publish(app: App, post_id: int, channel_ids: list[int], by_user: int,
                  pub_ids: dict[int, int] | None = None) -> list[Result]:
    """Сразу публикует. pub_ids - уже созданные отложенные записи {канал: id записи}."""
    post = await P.get_post(app, post_id)
    if post is None:
        return [Result(cid, str(cid), False, "пост удалён") for cid in channel_ids]
    buttons = await P.get_buttons(app, post_id)
    results: list[Result] = []
    for cid in channel_ids:
        channel = await app.channel(cid)
        title = channel.title if channel else str(cid)
        pub_id = (pub_ids or {}).get(cid)
        try:
            msg = await _call(lambda: send_post(app, cid, post, buttons))
        except (TelegramAPIError, RenderError) as e:
            err = human_error(e)
            if isinstance(e, TelegramAPIError):
                await channel_rights(app, cid)  # может, права забрали, обновим отметку в списке
            if pub_id:
                await app.db.execute("UPDATE publications SET status = 'failed', error = ? WHERE id = ?",
                                     (err[:300], pub_id))
            else:
                await app.db.execute(
                    "INSERT INTO publications(post_id, channel_id, status, error, by_user, created_at) "
                    "VALUES (?, ?, 'failed', ?, ?, ?)", (post_id, cid, err[:300], by_user, now()))
            results.append(Result(cid, title, False, err))
            continue
        ts = now()
        delete_at = ts + post.delete_after * 60 if post.delete_after else None
        if pub_id:
            await app.db.execute(
                "UPDATE publications SET status = 'sent', message_id = ?, sent_at = ?, delete_at = ?, error = NULL "
                "WHERE id = ?", (msg.message_id, ts, delete_at, pub_id))
        else:
            await app.db.execute(
                "INSERT INTO publications(post_id, channel_id, message_id, status, sent_at, delete_at, by_user, "
                "created_at) VALUES (?, ?, ?, 'sent', ?, ?, ?, ?)",
                (post_id, cid, msg.message_id, ts, delete_at, by_user, ts))
        info = (channel.post_link(msg.message_id) if channel else None) or "отправлено"
        if post.pin:
            try:
                await app.bot.pin_chat_message(cid, msg.message_id, disable_notification=bool(post.silent))
            except TelegramAPIError as e:
                info += f" (не закрепил: {human_error(e)})"
        results.append(Result(cid, title, True, info, msg.message_id))
    return results


async def schedule(app: App, post_id: int, channel_ids: list[int], run_at: int, by_user: int) -> None:
    ts = now()
    await app.db.executemany(
        "INSERT INTO publications(post_id, channel_id, status, run_at, by_user, created_at) "
        "VALUES (?, ?, 'scheduled', ?, ?, ?)", ((post_id, cid, run_at, by_user, ts) for cid in channel_ids))


async def cancel_scheduled(app: App, post_id: int) -> int:
    rows = await P.publications(app, post_id, "scheduled")
    await app.db.execute("DELETE FROM publications WHERE post_id = ? AND status = 'scheduled'", (post_id,))
    return len(rows)


async def update_everywhere(app: App, post_id: int, old_has_media: bool | None = None) -> list[Result]:
    """Правит пост во всех каналах, где он сейчас висит. old_has_media - было ли медиа до правки."""
    post = await P.get_post(app, post_id)
    assert post is not None
    buttons = await P.get_buttons(app, post_id)
    old = post.media_id is not None if old_has_media is None else old_has_media
    results: list[Result] = []
    for pub in await P.publications(app, post_id, "sent"):
        channel = await app.channel(pub.channel_id)
        title = channel.title if channel else str(pub.channel_id)
        try:
            await _call(lambda: edit_post(app, pub.channel_id, pub.message_id or 0, post, buttons, old))
            link = channel.post_link(pub.message_id or 0) if channel else None
            results.append(Result(pub.channel_id, title, True, link or "изменено"))
        except (TelegramAPIError, RenderError) as e:
            results.append(Result(pub.channel_id, title, False, human_error(e)))
    return results


async def delete_everywhere(app: App, post_id: int) -> list[Result]:
    results: list[Result] = []
    for pub in await P.publications(app, post_id, "sent"):
        results.append(await delete_one(app, pub))
    return results


async def delete_one(app: App, pub: Publication) -> Result:
    channel = await app.channel(pub.channel_id)
    title = channel.title if channel else str(pub.channel_id)
    try:
        await _call(lambda: app.bot.delete_message(pub.channel_id, pub.message_id or 0))
        await app.db.execute("UPDATE publications SET status = 'deleted', delete_at = NULL WHERE id = ?", (pub.id,))
        return Result(pub.channel_id, title, True, "удалено")
    except TelegramAPIError as e:
        err = human_error(e)
        if err == "сообщения уже нет":
            await app.db.execute("UPDATE publications SET status = 'deleted', delete_at = NULL WHERE id = ?",
                                 (pub.id,))
            return Result(pub.channel_id, title, True, "уже было удалено")
        await app.db.execute("UPDATE publications SET delete_at = NULL, error = ? WHERE id = ?", (err[:300], pub.id))
        return Result(pub.channel_id, title, False, err)


def results_text(results: list[Result], ok_word: str = "Опубликовано") -> str:
    lines = []
    for r in results:
        if r.ok:
            link = r.info if r.info.startswith("http") else ""
            name = f'<a href="{escape(link)}">{escape(r.title)}</a>' if link else escape(r.title)
            extra = "" if link else f" ({escape(r.info)})"
            lines.append(f"✅ {name}{extra}")
        else:
            lines.append(f"❌ {escape(r.title)}: {escape(r.info)}")
    good = sum(r.ok for r in results)
    head = f"{ok_word}: {good} из {len(results)}" if results else "Ничего не нашлось"
    return head + ("\n\n" + "\n".join(lines) if lines else "")


# ---------- фоновая работа ----------

async def run_due(app: App) -> None:
    """Выпускает отложенные посты и удаляет те, у которых вышло время."""
    ts = now()
    due = await app.db.fetchall(
        "SELECT * FROM publications WHERE status = 'scheduled' AND run_at <= ? ORDER BY run_at, id LIMIT 50", (ts,))
    groups: dict[tuple[int, int], dict[int, int]] = {}
    for row in due:
        pub = from_row(Publication, row)
        groups.setdefault((pub.post_id, pub.by_user or 0), {})[pub.channel_id] = pub.id
    for (post_id, by_user), pub_ids in groups.items():
        # сразу помечаем, чтобы при падении не выпустить второй раз
        await app.db.executemany("UPDATE publications SET status = 'sending' WHERE id = ?",
                                 ((pid,) for pid in pub_ids.values()))
        results = await publish(app, post_id, list(pub_ids), by_user, pub_ids)
        post = await P.get_post(app, post_id)
        name = escape(post.name) if post else f"#{post_id}"
        if by_user:
            await app.notify(by_user, f"⏰ Отложенный пост #{post_id} · {name}\n" + results_text(results))

    gone = await app.db.fetchall(
        "SELECT * FROM publications WHERE status = 'sent' AND delete_at IS NOT NULL AND delete_at <= ? LIMIT 50",
        (ts,))
    for row in gone:
        pub = from_row(Publication, row)
        r = await delete_one(app, pub)
        if not r.ok and pub.by_user:
            await app.notify(pub.by_user, f"⚠️ Не смог автоудалить пост #{pub.post_id} в {escape(r.title)}: "
                                          f"{escape(r.info)}")


async def recover_sending(app: App) -> None:
    """После перезапуска: то, что застряло в sending, считаем неудачным, чтобы не задвоить."""
    await app.db.execute("UPDATE publications SET status = 'failed', error = 'бот перезапустился во время отправки' "
                         "WHERE status = 'sending'")


async def channel_rights(app: App, chat_id: int) -> tuple[Channel | None, str | None]:
    """Спрашивает у Telegram, что бот может в чате, и сохраняет. -> (канал, ошибка)."""
    from aiogram.enums import ChatMemberStatus
    try:
        chat = await app.bot.get_chat(chat_id)
        member = await app.bot.get_chat_member(chat_id, app.bot_id)
    except TelegramAPIError as e:
        await app.db.execute("UPDATE channels SET is_present = 0, can_post = 0, updated_at = ? WHERE id = ?",
                             (now(), chat_id))
        return await app.channel(chat_id), human_error(e)
    status = member.status
    present = status not in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
    await save_channel(app, chat.id, chat.title or chat.full_name or str(chat.id), chat.username, chat.type,
                       member, present, None)
    return await app.channel(chat.id), None


async def save_channel(app: App, chat_id: int, title: str, username: str | None, chat_type: str, member,
                       present: bool, added_by: int | None) -> None:
    from aiogram.enums import ChatMemberStatus
    is_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    g = lambda name: bool(getattr(member, name, False))  # noqa: E731
    if chat_type == "channel":
        can_post = is_admin and g("can_post_messages")
        can_edit = is_admin and g("can_edit_messages")
        can_delete = is_admin and g("can_delete_messages")
        can_pin = is_admin and g("can_edit_messages")
    else:
        restricted = member.status == ChatMemberStatus.RESTRICTED and not g("can_send_messages")
        can_post = present and not restricted
        can_edit = present
        can_delete = present
        can_pin = is_admin and g("can_pin_messages")
    await app.db.execute(
        "INSERT INTO channels(id, title, username, type, can_post, can_edit, can_delete, can_pin, is_present, "
        "added_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title = excluded.title, username = excluded.username, type = excluded.type, "
        "can_post = excluded.can_post, can_edit = excluded.can_edit, can_delete = excluded.can_delete, "
        "can_pin = excluded.can_pin, is_present = excluded.is_present, updated_at = excluded.updated_at, "
        "added_by = COALESCE(channels.added_by, excluded.added_by)",
        (chat_id, title, username, chat_type, int(can_post), int(can_edit), int(can_delete), int(can_pin),
         int(present), added_by, now()))
