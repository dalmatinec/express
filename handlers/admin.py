"""
Команды администратора. Никаких форм и меню — только команды.
"""

import html

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database import db
from filters import IsAdmin, IsSuperAdmin, get_group_id
from texts import DEFAULTS, DISABLED, get_raw, render

router = Router()
router.message.filter(IsAdmin())

private = F.chat.type == "private"

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

<b>Пользователи</b>
/ban <code>ID</code>, /unban <code>ID</code> — блокировка
В группе: ответьте на сообщение пользователя /ban, /unban или /who

<b>Администраторы</b> (только супер-админ)
/addadmin <code>ID</code>, /deladmin <code>ID</code>, /admins

/stats — статистика

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


# ==================== ПОЛЬЗОВАТЕЛИ ====================

@router.message(Command("ban"), private)
async def ban(message: Message, command: CommandObject):
    user_id = parse_id(command)
    if user_id is None:
        await message.answer("Формат: /ban <code>ID</code>")
        return
    db.set_banned(user_id, True)
    await message.answer(f"🚫 Пользователь <code>{user_id}</code> заблокирован.")


@router.message(Command("unban"), private)
async def unban(message: Message, command: CommandObject):
    user_id = parse_id(command)
    if user_id is None:
        await message.answer("Формат: /unban <code>ID</code>")
        return
    db.set_banned(user_id, False)
    await message.answer(f"✅ Пользователь <code>{user_id}</code> разблокирован.")


@router.message(Command("stats"), private)
async def stats(message: Message):
    s = db.get_stats()
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {s['users']}\n"
        f"📨 Переслано сообщений: {s['messages']}\n"
        f"🚫 Заблокировано: {s['banned']}\n"
        f"👮 Администраторов: {s['admins']}"
    )


# ==================== АДМИНИСТРАТОРЫ ====================

@router.message(Command("addadmin"), private, IsSuperAdmin())
async def add_admin(message: Message, command: CommandObject):
    admin_id = parse_id(command)
    if admin_id is None:
        await message.answer("Формат: /addadmin <code>ID</code>")
        return
    added = db.add_admin(admin_id)
    await message.answer("✅ Администратор добавлен." if added else "ℹ️ Уже администратор.")


@router.message(Command("deladmin"), private, IsSuperAdmin())
async def del_admin(message: Message, command: CommandObject):
    admin_id = parse_id(command)
    if admin_id is None:
        await message.answer("Формат: /deladmin <code>ID</code>")
        return
    removed = db.remove_admin(admin_id)
    await message.answer("✅ Администратор удалён." if removed else "ℹ️ Такого администратора нет.")


@router.message(Command("admins"), private, IsSuperAdmin())
async def list_admins(message: Message):
    admins = db.get_admins()
    lines = "\n".join(f"• <code>{a}</code>" for a in admins) or "—"
    await message.answer(f"👮 <b>Администраторы</b>\n\n{lines}")
