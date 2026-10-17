"""События каналов: бота добавили, дали или забрали права, выкинули. И нажатия всплывающих кнопок в постах."""
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from ..app import App, now
from ..services import posts as P
from ..services.publisher import save_channel

router = Router(name="chats")

GROUPS = {"group", "supergroup", "channel"}
GONE = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


@router.my_chat_member(F.chat.type.in_(GROUPS))
async def bot_status(event: ChatMemberUpdated, app: App) -> None:
    chat, new = event.chat, event.new_chat_member
    present = new.status not in GONE
    was = await app.channel(chat.id)
    actor = event.from_user
    by_admin = app.can_manage_channels(actor.id)

    if was is None and not by_admin:
        return  # бота добавил посторонний человек, такой чат не подключаем
    await save_channel(app, chat.id, chat.title or str(chat.id), chat.username, chat.type, new, present,
                       actor.id if by_admin else None)
    channel = await app.channel(chat.id)
    if channel is None:
        return
    title = escape(channel.title)

    if actor.id in app.connecting:
        return  # человек сейчас подключает канал кнопкой, ему и так всё покажут
    if not present:
        if was is not None and was.is_present:
            await app.notify_owners(f"⚠️ Бота убрали из {title}. Публиковать туда больше не получится.")
        return
    if by_admin:
        if channel.ok and not (was and was.ok):
            await app.notify(actor.id, f"✅ {title} подключён, можно публиковать.")
        elif not channel.ok and new.status != ChatMemberStatus.MEMBER:
            await app.notify(actor.id, f"⚠️ Я в {title}, но без права публиковать сообщения. Выдайте его.")


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: Message, app: App) -> None:
    """Группа стала супергруппой и получила новый id."""
    old, new = message.chat.id, message.migrate_to_chat_id
    await app.db.execute(
        "INSERT OR REPLACE INTO channels(id, title, username, type, can_post, can_edit, can_delete, can_pin, "
        "is_present, added_by, updated_at) SELECT ?, title, username, 'supergroup', can_post, can_edit, can_delete, "
        "can_pin, is_present, added_by, ? FROM channels WHERE id = ?", (new, now(), old))
    await app.db.execute("UPDATE admin_channels SET channel_id = ? WHERE channel_id = ?", (new, old))
    await app.db.execute("DELETE FROM channels WHERE id = ?", (old,))


@router.callback_query(F.data.startswith("pp:"))
async def popup(cq: CallbackQuery, app: App) -> None:
    text = await P.popup_text(app, (cq.data or "")[3:])
    await cq.answer(text or "Эта кнопка больше не работает", show_alert=True)
