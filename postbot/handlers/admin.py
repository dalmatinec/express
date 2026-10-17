"""Админка: каналы, админы, бэкап, проверка премиум-эмодзи."""
import tempfile
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, ChatAdministratorRights, KeyboardButton, KeyboardButtonRequestChat, KeyboardButtonRequestUsers,
    Message, ReplyKeyboardMarkup,
)

from .. import ui
from ..app import App, now
from ..services.backup import make_backup, restore_backup, send_backup
from ..services.publisher import channel_rights
from ..states import Adm
from ..utils.text import short
from .common import clicked, parts

router = Router(name="admin")
router.message.filter(F.chat.type == "private")

REQ_CHANNEL, REQ_GROUP, REQ_USER = 1, 2, 3

# права, которые Telegram сам предложит выдать боту, когда выбираете канал кнопкой
BOT_RIGHTS = ChatAdministratorRights(
    is_anonymous=False, can_manage_chat=False, can_delete_messages=True, can_manage_video_chats=False,
    can_restrict_members=False, can_promote_members=False, can_change_info=False, can_invite_users=False,
    can_post_stories=False, can_edit_stories=False, can_delete_stories=False, can_send_welcome_messages=False,
    can_post_messages=True, can_edit_messages=True, can_pin_messages=True,
)
GROUP_RIGHTS = ChatAdministratorRights(
    is_anonymous=False, can_manage_chat=False, can_delete_messages=True, can_manage_video_chats=False,
    can_restrict_members=False, can_promote_members=False, can_change_info=False, can_invite_users=False,
    can_post_stories=False, can_edit_stories=False, can_delete_stories=False, can_send_welcome_messages=False,
    can_pin_messages=True,
)


def rights_line(flag: int, name: str) -> str:
    return f"{'✅' if flag else '❌'} {name}"


async def show_main(app: App, uid: int, edit: Message | None = None, note: str = "") -> None:
    owner = app.is_owner(uid)
    channels = await app.all_channels()
    rows = []
    if app.can_manage_channels(uid):
        rows.append([ui.b(f"📢 Каналы ({len(channels)})", "a:ch")])
    if owner:
        rows.append([ui.b(f"👮 Админы ({len(app.admins)})", "a:adm")])
        rows.append([ui.b("💾 Бэкап", "a:bk")])
    role = "владелец" if owner else "админ"
    text = (f"⚙️ Админка\n\nВы: {role}\nБот: @{escape(app.bot_username)}\nКаналов: {len(channels)}\n"
            f"Часовой пояс: {app.config.tz_name}")
    if note:
        text = f"{note}\n\n{text}"
    await ui.show(app, uid, text, ui.kb(rows), edit=edit)


# ---------- каналы ----------

async def show_channels(app: App, uid: int, edit: Message | None = None, note: str = "") -> None:
    channels = await app.all_channels()
    rows = [[ui.b(f"{'✅' if c.ok else '⚠️'} {c.title}", f"a:chc:{c.id}")] for c in channels]
    rows.append([ui.b("➕ Подключить канал или группу", "a:chadd", "success")])
    rows.append([ui.b("🔙 Админка", "a:main")])
    text = "📢 Каналы и группы, куда бот может постить:" if channels else "📢 Каналов пока нет."
    if any(not c.ok for c in channels):
        text += "\n\n⚠️ - бота там нет или у него нет права публиковать."
    if note:
        text = f"{note}\n\n{text}"
    await ui.show(app, uid, text, ui.kb(rows), edit=edit)


async def show_channel(app: App, uid: int, channel_id: int, edit: Message | None = None, note: str = "") -> None:
    c = await app.channel(channel_id)
    if c is None:
        await show_channels(app, uid, edit=edit, note="Канал уже отключён.")
        return
    posts = await app.db.fetchval("SELECT COUNT(*) FROM publications WHERE channel_id = ? AND status = 'sent'",
                                  (c.id,))
    lines = [f"📢 {escape(c.title)}", f"{c.kind_name.capitalize()}, ID <code>{c.id}</code>"]
    if c.username:
        lines.append(f"@{escape(c.username)}")
    lines.append("")
    if not c.is_present:
        lines.append("❌ Бота там нет. Добавьте его админом заново.")
    else:
        lines += [rights_line(c.can_post, "Публиковать"), rights_line(c.can_edit, "Изменять посты"),
                  rights_line(c.can_delete, "Удалять посты"), rights_line(c.can_pin, "Закреплять")]
    lines.append(f"\nПостов бота там сейчас: {posts}\nПроверено: {app.fmt(c.updated_at)}")
    if note:
        lines.insert(0, note + "\n")
    rows = [[ui.b("🔄 Проверить права", f"a:chr:{c.id}")],
            [ui.b("🔌 Отключить", f"a:chd:{c.id}", "danger")],
            [ui.b("🔙 Каналы", "a:ch")]]
    await ui.show(app, uid, "\n".join(lines), ui.kb(rows), edit=edit)


def picker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📢 Выбрать канал", request_chat=KeyboardButtonRequestChat(
            request_id=REQ_CHANNEL, chat_is_channel=True, bot_administrator_rights=BOT_RIGHTS,
            request_title=True, request_username=True), style="primary")],
        [KeyboardButton(text="👥 Выбрать группу", request_chat=KeyboardButtonRequestChat(
            request_id=REQ_GROUP, chat_is_channel=False, bot_administrator_rights=GROUP_RIGHTS,
            request_title=True, request_username=True))],
        [KeyboardButton(text=ui.CANCEL_TEXT, style="danger")],
    ], resize_keyboard=True, one_time_keyboard=True)


async def ask_channel(app: App, uid: int, state: FSMContext) -> None:
    await state.set_state(Adm.channel)
    app.start_connecting(uid)
    await ui.show(app, uid,
                  "➕ Подключение канала\n\n"
                  "Самое простое: нажмите внизу 📢 Выбрать канал. Telegram сам покажет ваши каналы и добавит "
                  "туда бота с нужными правами.\n\n"
                  "Ещё можно:\n• добавить бота админом в канал руками, он подключится сам\n"
                  "• переслать сюда любой пост из канала\n• прислать @username или ID канала",
                  picker_keyboard())


async def connect_chat(app: App, uid: int, chat_id: int, state: FSMContext) -> None:
    channel, error = await channel_rights(app, chat_id)
    await state.clear()
    app.stop_connecting(uid, grace=60)
    if channel is None:
        await ui.show(app, uid, f"⚠️ Не получилось: {escape(error or 'неизвестная ошибка')}.\n\n"
                                "Проверьте, что бот добавлен в канал админом.", ui.main_keyboard())
        return
    await app.db.execute("UPDATE channels SET added_by = COALESCE(added_by, ?) WHERE id = ?", (uid, channel.id))
    if channel.ok:
        note = f"✅ Подключено: {escape(channel.title)}"
    elif not channel.is_present:
        note = f"⚠️ {escape(channel.title)}: бота там нет. Добавьте его админом и проверьте ещё раз."
    else:
        note = f"⚠️ {escape(channel.title)}: у бота нет права публиковать. Дайте его и нажмите Проверить права."
    # вернуть нижнюю клавиатуру меню и показать канал
    await ui.show(app, uid, note, ui.main_keyboard())
    await show_channel(app, uid, channel.id)


@router.message(F.chat_shared, StateFilter("*"))
async def got_chat_shared(message: Message, state: FSMContext, app: App) -> None:
    await ui.drop_input(message)
    if not app.can_manage_channels(message.from_user.id):
        return
    await connect_chat(app, message.from_user.id, message.chat_shared.chat_id, state)


@router.message(Adm.channel)
async def got_channel(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    await ui.drop_input(message)
    chat_id: int | str | None = None
    origin = message.forward_origin
    if origin is not None and getattr(origin, "chat", None) is not None:
        chat_id = origin.chat.id
    elif message.text:
        raw = message.text.strip()
        if raw.lstrip("-").isdigit():
            chat_id = int(raw)
        elif raw.startswith("@") or "t.me/" in raw:
            chat_id = "@" + raw.rstrip("/").split("/")[-1].lstrip("@")
    if chat_id is None:
        await ui.show(app, uid, "⚠️ Не понял, какой это канал. Перешлите пост из канала, пришлите @username или ID, "
                                "или нажмите кнопку внизу.", picker_keyboard())
        return
    if isinstance(chat_id, str):
        try:
            chat = await app.bot.get_chat(chat_id)
            chat_id = chat.id
        except TelegramAPIError:
            await ui.show(app, uid, "⚠️ Такой канал не нашёлся или бота там нет.", picker_keyboard())
            return
    await connect_chat(app, uid, int(chat_id), state)


# ---------- админы ----------

async def show_admins(app: App, uid: int, edit: Message | None = None, note: str = "") -> None:
    rows = [[ui.b(f"👮 {short(a.name or str(a.user_id), 30)}", f"a:admc:{a.user_id}")]
            for a in sorted(app.admins.values(), key=lambda x: x.created_at)]
    rows.append([ui.b("➕ Добавить админа", "a:admadd", "success")])
    rows.append([ui.b("🔙 Админка", "a:main")])
    owners = ", ".join(f"<code>{o}</code>" for o in sorted(app.config.owner_ids))
    text = (f"👮 Админы\n\nВладельцы (из .env): {owners}\nУ владельцев все права.\n\n"
            "Админ может создавать посты и публиковать их в разрешённые каналы.")
    if note:
        text = f"{note}\n\n{text}"
    await ui.show(app, uid, text, ui.kb(rows), edit=edit)


async def show_admin(app: App, uid: int, admin_id: int, edit: Message | None = None, note: str = "") -> None:
    a = app.admins.get(admin_id)
    if a is None:
        await show_admins(app, uid, edit=edit, note="Такого админа уже нет.")
        return
    channels = await app.all_channels()
    allowed = {r[0] for r in await app.db.fetchall("SELECT channel_id FROM admin_channels WHERE user_id = ?",
                                                    (admin_id,))}
    rows = [[ui.b(f"{ui.mark(a.all_channels)} Все каналы", f"a:admall:{admin_id}")]]
    if not a.all_channels:
        rows += [[ui.b(f"{ui.mark(c.id in allowed)} {c.title}", f"a:admt:{admin_id}:{c.id}")] for c in channels]
    rows.append([ui.b(f"{ui.mark(a.can_channels)} Может подключать и отключать каналы", f"a:admcc:{admin_id}")])
    rows.append([ui.b("🗑 Убрать из админов", f"a:admd:{admin_id}", "danger")])
    rows.append([ui.b("🔙 Админы", "a:adm")])
    access = "все каналы" if a.all_channels else f"выбранные ({len(allowed & {c.id for c in channels})})"
    text = (f"👮 {escape(a.name or str(a.user_id))}\nID <code>{a.user_id}</code>\n"
            f"Добавлен {app.fmt(a.created_at)}\n\nДоступ: {access}\n"
            "Отметьте каналы, куда этому админу можно публиковать.")
    if note:
        text = f"{note}\n\n{text}"
    await ui.show(app, uid, text, ui.kb(rows), edit=edit)


def user_picker() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Выбрать человека", request_users=KeyboardButtonRequestUsers(
            request_id=REQ_USER, user_is_bot=False, max_quantity=1, request_name=True, request_username=True),
            style="primary")],
        [KeyboardButton(text=ui.CANCEL_TEXT, style="danger")],
    ], resize_keyboard=True, one_time_keyboard=True)


async def add_admin(app: App, uid: int, user_id: int, name: str, state: FSMContext) -> None:
    await state.clear()
    if app.is_owner(user_id):
        await ui.show(app, uid, "Это владелец, у него и так все права.", ui.main_keyboard())
        await show_admins(app, uid)
        return
    if user_id not in app.admins:
        await app.db.execute("INSERT INTO admins(user_id, name, added_by, created_at) VALUES (?, ?, ?, ?)",
                             (user_id, name, uid, now()))
    elif name:
        await app.db.execute("UPDATE admins SET name = ? WHERE user_id = ?", (name, user_id))
    await app.load()
    told = await app.notify(user_id, "👋 Вам дали доступ к боту постов. Нажмите /start")
    note = "✅ Админ добавлен." + ("" if told else "\nОн ещё не запускал бота, пусть нажмёт /start.")
    await ui.show(app, uid, note, ui.main_keyboard())
    await show_admin(app, uid, user_id)


@router.message(F.users_shared, StateFilter("*"))
async def got_user_shared(message: Message, state: FSMContext, app: App) -> None:
    await ui.drop_input(message)
    if not app.is_owner(message.from_user.id) or not message.users_shared.users:
        return
    user = message.users_shared.users[0]
    name = " ".join(x for x in (user.first_name, user.last_name) if x) or (f"@{user.username}" if user.username else "")
    await add_admin(app, message.from_user.id, user.user_id, name, state)


@router.message(Adm.admin)
async def got_admin(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    await ui.drop_input(message)
    origin = message.forward_origin
    if origin is not None and getattr(origin, "sender_user", None) is not None:
        u = origin.sender_user
        await add_admin(app, uid, u.id, u.full_name, state)
        return
    raw = (message.text or "").strip()
    if raw.isdigit():
        await add_admin(app, uid, int(raw), "", state)
        return
    await ui.show(app, uid, "⚠️ Нажмите внизу 👤 Выбрать человека, пришлите его числовой ID или перешлите его "
                            "сообщение (если он не скрыл пересылку).", user_picker())


# ---------- бэкап ----------

async def show_backup(app: App, uid: int, edit: Message | None = None, note: str = "") -> None:
    hour = int(app.setting("backup_hour", 4))
    last = app.setting("last_backup")
    text = (f"💾 Бэкап\n\nКаждый день в {hour:02d}:00 бот делает бэкап (база и все медиа) и присылает файл "
            "владельцам сюда в личку. Прошлый файл из чата удаляется, на сервере тоже лежит один файл.\n\n"
            f"Последний: {app.fmt(last) if last else 'ещё не было'}\n\n"
            "Переезд на другой сервер или новый токен: запустите бота там, Админка, Бэкап, Восстановить, "
            "и пришлите файл. Всё вернётся: посты, кнопки, каналы, админы, история.")
    if note:
        text = f"{note}\n\n{text}"
    rows = [[ui.b("💾 Сделать бэкап сейчас", "a:bkgo", "success")],
            [ui.b("♻️ Восстановить из файла", "a:bkr", "danger")],
            [ui.b(f"🕓 Время автобэкапа: {hour:02d}:00", "a:bkh")],
            [ui.b("🔙 Админка", "a:main")]]
    await ui.show(app, uid, text, ui.kb(rows), edit=edit)


@router.message(Adm.restore)
async def got_restore(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    await ui.drop_input(message)
    back = ui.kb([[ui.b("🔙 Назад", "a:bk")]])
    doc = message.document
    if doc is None or not (doc.file_name or "").lower().endswith(".zip"):
        await ui.show(app, uid, "⚠️ Нужен файл бэкапа .zip", back)
        return
    if (doc.file_size or 0) > 20 * 1024 * 1024:
        await ui.show(app, uid, "⚠️ Файл больше 20 МБ, бот не может его скачать. Восстановите на сервере командой "
                                "из инструкции (install.sh с путём к файлу).", back)
        return
    await state.clear()
    await ui.show(app, uid, "♻️ Восстанавливаю...", None)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "backup.zip"
        try:
            await app.bot.download(doc.file_id, destination=path)
            await restore_backup(app, path)
        except (TelegramAPIError, ValueError, OSError) as e:
            await show_backup(app, uid, note=f"⚠️ Не получилось: {escape(str(e))}")
            return
    # бот мог смениться, проверим права во всех каналах уже от лица этого бота
    channels = await app.all_channels()
    bad = []
    for c in channels:
        fresh, _ = await channel_rights(app, c.id)
        if fresh is None or not fresh.ok:
            bad.append(c.title)
    await app.set_setting("bot_id", app.bot_id)
    posts = await app.db.fetchval("SELECT COUNT(*) FROM posts WHERE is_draft = 0")
    note = f"✅ Восстановлено. Постов в истории: {posts}, каналов: {len(channels)}."
    if bad:
        note += ("\n\n⚠️ В этих каналах бота нет или нет права публиковать, добавьте его туда админом:\n"
                 + "\n".join(f"• {escape(t)}" for t in bad))
    await show_main(app, uid, note=note)


# ---------- нажатия ----------

@router.callback_query(F.data.startswith("a:"))
async def admin_cb(cq: CallbackQuery, state: FSMContext, app: App) -> None:
    p = parts(cq)
    action = p[1]
    arg = int(p[2]) if len(p) > 2 and p[2].lstrip("-").isdigit() else 0
    uid, msg = cq.from_user.id, clicked(cq)
    owner = app.is_owner(uid)
    await state.clear()
    app.stop_connecting(uid)

    channel_actions = {"ch", "chc", "chr", "chd", "chdy", "chadd"}
    owner_actions = {"adm", "admc", "admadd", "admall", "admt", "admcc", "admd", "admdy", "bk", "bkgo", "bkr",
                     "bkh", "bkhs"}
    if (action in channel_actions and not app.can_manage_channels(uid)) or (action in owner_actions and not owner):
        await cq.answer("Это может только владелец", show_alert=True)
        return

    if action == "main":
        await cq.answer()
        await show_main(app, uid, edit=msg)

    elif action == "ch":
        await cq.answer()
        await show_channels(app, uid, edit=msg)

    elif action == "chc":
        await cq.answer()
        await show_channel(app, uid, arg, edit=msg)

    elif action == "chr":
        _, error = await channel_rights(app, arg)
        await cq.answer("Проверено" if not error else error[:190], show_alert=bool(error))
        await show_channel(app, uid, arg, edit=msg)

    elif action == "chd":
        await cq.answer()
        c = await app.channel(arg)
        await ui.show(app, uid, f"Отключить {escape(c.title if c else str(arg))}?\n\n"
                                "Бот выйдет из канала и уберёт его из списка. Посты в канале останутся.",
                      ui.kb([[ui.b("🔌 Да, отключить", f"a:chdy:{arg}", "danger")],
                             [ui.b("🔙 Нет", f"a:chc:{arg}")]]), edit=msg)

    elif action == "chdy":
        try:
            await app.bot.leave_chat(arg)
        except TelegramAPIError:
            pass
        await app.db.execute("DELETE FROM channels WHERE id = ?", (arg,))
        await cq.answer("Отключено")
        await show_channels(app, uid, edit=msg)

    elif action == "chadd":
        await cq.answer()
        await ask_channel(app, uid, state)

    elif action == "adm":
        await cq.answer()
        await show_admins(app, uid, edit=msg)

    elif action == "admc":
        await cq.answer()
        await show_admin(app, uid, arg, edit=msg)

    elif action == "admadd":
        await cq.answer()
        await state.set_state(Adm.admin)
        await ui.show(app, uid, "➕ Новый админ\n\nНажмите внизу 👤 Выбрать человека, или пришлите его числовой ID, "
                                "или перешлите любое его сообщение.", user_picker())

    elif action in ("admall", "admcc"):
        a = app.admins.get(arg)
        if a is not None:
            col = "all_channels" if action == "admall" else "can_channels"
            await app.db.execute(f"UPDATE admins SET {col} = ? WHERE user_id = ?",
                                 (0 if getattr(a, col) else 1, arg))
            await app.load()
        await cq.answer()
        await show_admin(app, uid, arg, edit=msg)

    elif action == "admt":
        cid = int(p[3])
        exists = await app.db.fetchval("SELECT 1 FROM admin_channels WHERE user_id = ? AND channel_id = ?",
                                       (arg, cid))
        if exists:
            await app.db.execute("DELETE FROM admin_channels WHERE user_id = ? AND channel_id = ?", (arg, cid))
        else:
            await app.db.execute("INSERT OR IGNORE INTO admin_channels(user_id, channel_id) VALUES (?, ?)",
                                 (arg, cid))
        await cq.answer()
        await show_admin(app, uid, arg, edit=msg)

    elif action == "admd":
        await cq.answer()
        a = app.admins.get(arg)
        await ui.show(app, uid, f"Убрать {escape(a.name if a and a.name else str(arg))} из админов?",
                      ui.kb([[ui.b("🗑 Да, убрать", f"a:admdy:{arg}", "danger")],
                             [ui.b("🔙 Нет", f"a:admc:{arg}")]]), edit=msg)

    elif action == "admdy":
        await app.db.execute("DELETE FROM admins WHERE user_id = ?", (arg,))
        await app.load()
        await cq.answer("Убран")
        await show_admins(app, uid, edit=msg)

    elif action == "bk":
        await cq.answer()
        await show_backup(app, uid, edit=msg)

    elif action == "bkgo":
        await cq.answer("Делаю бэкап...")
        path = await make_backup(app)
        errors = await send_backup(app, path, "Бэкап по кнопке.")
        note = "✅ Бэкап готов и отправлен." if not errors else "⚠️ " + "\n".join(escape(e) for e in errors)
        await show_backup(app, uid, note=note)

    elif action == "bkr":
        await cq.answer()
        await state.set_state(Adm.restore)
        await ui.show(app, uid, "♻️ Пришлите файл бэкапа (.zip), который присылал бот.\n\n"
                                "⚠️ Всё, что сейчас в боте, заменится содержимым файла. Текущая база сохранится "
                                "рядом копией на всякий случай.", ui.kb([[ui.b("🔙 Назад", "a:bk")]]), edit=msg)

    elif action == "bkh":
        await cq.answer()
        hours = [ui.b(f"{h:02d}", f"a:bkhs:{h}") for h in range(24)]
        rows = [hours[i:i + 6] for i in range(0, 24, 6)] + [[ui.b("🔙 Назад", "a:bk")]]
        await ui.show(app, uid, f"🕓 Во сколько делать автобэкап? Время по поясу {app.config.tz_name}.",
                      ui.kb(rows), edit=msg)

    elif action == "bkhs":
        await app.set_setting("backup_hour", max(0, min(23, arg)))
        await cq.answer("Сохранено")
        await show_backup(app, uid, edit=msg)

    else:
        await cq.answer()

