"""
Команды администратора. Никаких форм и меню — только команды.
"""

import html

from typing import Optional

from aiogram import Bot, Router, F
from aiogram.filters import Command, CommandObject, or_f
from aiogram.types import Message

from config import SUPER_ADMIN_ID
from database import db
from filters import IsAdmin, IsSuperAdmin, IsWorkGroup, get_group_id
from handlers.group import linked_user
from texts import DEFAULTS, DISABLED, get_raw, render

router = Router()
router.message.filter(IsAdmin())

private = F.chat.type == "private"
# Бан и управление админами работают и в личке, и в рабочей группе
private_or_group = or_f(private, IsWorkGroup())

HELP = """<b>🛠 Команды администратора</b>

<b>Группа</b>
/setgroup — выполнить в группе, чтобы сделать её рабочей
/setgroup <code>ID</code> — задать группу по ID (в личке)
/group — текущая группа и проверка доступа

<b>Тексты</b>
/texts — все тексты и их ключи
/settext <code>ключ</code> <code>текст</code> — изменить текст (форматирование сохраняется)
/settext <code>ключ</code> - — отключить сообщение (для welcome, sent, banned)
/resettext <code>ключ</code> — вернуть текст по умолчанию

<b>Блокировка</b> (в личке или в группе)
/ban, /unban — реплаем на пересланное сообщение пользователя
/ban <code>ID</code>, /unban <code>ID</code> — по ID

<b>Администраторы</b> (только супер-админ, в личке или в группе)
/addadmin, /deladmin — реплаем на сообщение человека в группе
/addadmin <code>ID</code>, /deladmin <code>ID</code> — по ID
/admins — список админов с именами

<b>Как отвечать:</b> в группе нажмите «Ответить» (reply) на пересланное сообщение пользователя. Отвечать можно сколько угодно раз."""


def parse_id(command: CommandObject) -> int | None:
    if not command.args:
        return None
    try:
        return int(command.args.split()[0])
    except ValueError:
        return None


@router.message(Command("admin", "help"), private)
async def admin_help(message: Message):
    await message.answer(HELP)


# ==================== ГРУППА ====================

@router.message(Command("setgroup"))
async def set_group(message: Message, command: CommandObject):
    if message.chat.type in ("group", "supergroup"):
        group_id = message.chat.id
    else:
        group_id = parse_id(command)
        if group_id is None:
            await message.answer(
                "Выполните /setgroup в нужной группе или укажите ID: /setgroup <code>-1001234567890</code>"
            )
            return

    try:
        chat = await message.bot.get_chat(group_id)
    except Exception as e:
        await message.answer(f"❌ Бот не видит группу <code>{group_id}</code>: {html.escape(str(e))}")
        return

    db.set_setting("group_id", str(group_id))
    await message.answer(
        f"✅ Рабочая группа: <b>{html.escape(chat.title or '')}</b> (<code>{group_id}</code>)"
    )


@router.message(Command("group"), private)
async def show_group(message: Message):
    group_id = get_group_id()
    if group_id is None:
        await message.answer("Группа не задана. Добавьте бота в группу и выполните там /setgroup")
        return
    try:
        chat = await message.bot.get_chat(group_id)
        status = f"✅ доступна: <b>{html.escape(chat.title or '')}</b>"
    except Exception as e:
        status = f"❌ недоступна: {html.escape(str(e))}"
    await message.answer(f"Группа <code>{group_id}</code> — {status}")


# ==================== ТЕКСТЫ ====================

@router.message(Command("texts"), private)
async def list_texts(message: Message):
    chunks, current = [], "<b>📝 Тексты</b> (изменить: /settext ключ текст)\n"
    for key, info in DEFAULTS.items():
        block = (
            f"\n<b>{key}</b> — {html.escape(info['description'])}\n"
            f"<pre>{html.escape(get_raw(key))}</pre>\n"
        )
        if len(current) + len(block) > 3900:
            chunks.append(current)
            current = ""
        current += block
    chunks.append(current)
    for chunk in chunks:
        await message.answer(chunk)


@router.message(Command("settext"), private)
async def set_text(message: Message):
    # Берём HTML-версию, чтобы сохранить жирный/курсив/ссылки/премиум-эмодзи
    parts = message.html_text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /settext <code>ключ</code> <code>текст</code>\nКлючи: /texts")
        return

    key, value = parts[1], parts[2].strip()
    if key not in DEFAULTS:
        await message.answer(f"❌ Нет текста с ключом <code>{html.escape(key)}</code>. Список: /texts")
        return
    if value == DISABLED and key == "reply":
        await message.answer("❌ Текст ответа нельзя отключить.")
        return

    db.set_text(key, value)
    preview = render(key)
    await message.answer(f"✅ Текст <b>{key}</b> обновлён.")
    await message.answer(preview or "(сообщение отключено)")


@router.message(Command("resettext"), private)
async def reset_text(message: Message, command: CommandObject):
    key = (command.args or "").strip()
    if key not in DEFAULTS:
        await message.answer("Формат: /resettext <code>ключ</code>\nКлючи: /texts")
        return
    db.reset_text(key)
    await message.answer(f"✅ Текст <b>{key}</b> сброшен по умолчанию.")


# ==================== ИМЕНА ====================

def format_person(user_id: int, name: str = "", username: str = "") -> str:
    """«Имя (@username) — ID», чтобы было понятно, чей это ID."""
    parts = [html.escape(name) if name else "Без имени"]
    if username:
        parts.append(f"(@{username})")
    return f"{' '.join(parts)} — <code>{user_id}</code>"


async def person_info(bot: Bot, user_id: int) -> tuple[str, str]:
    """Имя и username по ID: из Telegram, иначе из базы."""
    try:
        chat = await bot.get_chat(user_id)
        name = " ".join(filter(None, [chat.first_name, chat.last_name])) or (chat.title or "")
        return name, chat.username or ""
    except Exception:
        pass
    known = db.get_user(user_id) or db.get_admin(user_id) or {}
    name = known.get("name") or " ".join(filter(None, [known.get("first_name"), known.get("last_name")]))
    return name or "", known.get("username") or ""


async def resolve_target(message: Message, command: CommandObject,
                         allow_staff: bool = False) -> Optional[tuple[int, str, str]]:
    """
    Кого касается команда: (ID, имя, username).
    - реплай на пересланное сообщение пользователя -> этот пользователь;
    - реплай на сообщение человека в группе (allow_staff) -> этот человек;
    - иначе ID из аргумента.
    """
    user_id = linked_user(message)
    if user_id is not None:
        return (user_id, *await person_info(message.bot, user_id))

    reply = message.reply_to_message
    if allow_staff and reply and reply.from_user and not reply.from_user.is_bot:
        u = reply.from_user
        return u.id, u.full_name, u.username or ""

    user_id = parse_id(command)
    if user_id is None:
        return None
    return (user_id, *await person_info(message.bot, user_id))


# ==================== БЛОКИРОВКА ====================

@router.message(Command("ban"), private_or_group)
async def ban(message: Message, command: CommandObject):
    target = await resolve_target(message, command)
    if target is None:
        await message.reply("Ответьте на сообщение пользователя командой /ban или укажите ID: /ban <code>123456</code>")
        return
    db.set_banned(target[0], True)
    await message.reply(f"🚫 Заблокирован: {format_person(*target)}")


@router.message(Command("unban"), private_or_group)
async def unban(message: Message, command: CommandObject):
    target = await resolve_target(message, command)
    if target is None:
        await message.reply("Ответьте на сообщение пользователя командой /unban или укажите ID: /unban <code>123456</code>")
        return
    db.set_banned(target[0], False)
    await message.reply(f"✅ Разблокирован: {format_person(*target)}")


# ==================== АДМИНИСТРАТОРЫ ====================

@router.message(Command("addadmin"), private_or_group, IsSuperAdmin())
async def add_admin(message: Message, command: CommandObject):
    target = await resolve_target(message, command, allow_staff=True)
    if target is None:
        await message.reply(
            "Ответьте на сообщение человека в группе командой /addadmin или укажите ID: /addadmin <code>123456</code>"
        )
        return
    if target[0] == SUPER_ADMIN_ID:
        await message.reply("ℹ️ Это супер-админ.")
        return
    added = db.add_admin(*target)
    status = "✅ Добавлен админ" if added else "ℹ️ Уже админ (имя обновлено)"
    await message.reply(f"{status}: {format_person(*target)}")


@router.message(Command("deladmin"), private_or_group, IsSuperAdmin())
async def del_admin(message: Message, command: CommandObject):
    target = await resolve_target(message, command, allow_staff=True)
    if target is None:
        await message.reply(
            "Ответьте на сообщение админа в группе командой /deladmin или укажите ID: /deladmin <code>123456</code>"
        )
        return
    stored = db.get_admin(target[0])
    if not db.remove_admin(target[0]):
        await message.reply(f"ℹ️ Не админ: {format_person(*target)}")
        return
    name = target[1] or (stored or {}).get("name") or ""
    username = target[2] or (stored or {}).get("username") or ""
    await message.reply(f"✅ Удалён админ: {format_person(target[0], name, username)}")


@router.message(Command("admins"), private_or_group, IsSuperAdmin())
async def list_admins(message: Message):
    lines = [f"👑 {format_person(SUPER_ADMIN_ID, *await person_info(message.bot, SUPER_ADMIN_ID))}"]
    for admin in db.get_admins():
        name, username = await person_info(message.bot, admin["admin_id"])
        name, username = name or admin["name"] or "", username or admin["username"] or ""
        # Имя могло поменяться — обновляем в базе
        if (name, username) != (admin["name"] or "", admin["username"] or ""):
            db.add_admin(admin["admin_id"], name, username)
        lines.append(f"👮 {format_person(admin['admin_id'], name, username)}")
    await message.reply("<b>Администраторы</b>\n\n" + "\n".join(lines))


@router.message(Command("addadmin", "deladmin", "admins"), private_or_group)
async def super_admin_only(message: Message):
    """Обычный админ вызвал команду супер-админа."""
    await message.reply("⛔ Управлять админами может только супер-админ.")
