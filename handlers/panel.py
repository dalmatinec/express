"""
Админ-панель на кнопках (в личке с ботом): /start или /admin.
Разделы: администраторы, блокировки, тексты, рабочая группа, справка.
"""

import html
from typing import Optional

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, or_f
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from filters import IsAdmin, is_super_admin, get_group_id
from handlers.admin import HELP
from handlers.common import format_person, person_info, apply_group, admins_text, refreshed_admins
from config import SUPER_ADMIN_ID
from texts import DEFAULTS, DISABLED, get_raw, render

router = Router()
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(IsAdmin())


class Panel(CallbackData, prefix="p"):
    action: str
    arg: str = ""


class Input(StatesGroup):
    admin_add = State()
    ban_add = State()
    text_edit = State()
    group_set = State()


def btn(kb: InlineKeyboardBuilder, text: str, action: str, arg: str = ""):
    kb.button(text=text, callback_data=Panel(action=action, arg=arg))


def back_kb(to: str = "main") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    btn(kb, "⬅️ Назад", to)
    return kb.as_markup()


def cancel_kb(to: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    btn(kb, "❌ Отмена", "cancel", to)
    return kb.as_markup()


async def show(target: Message | CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup]):
    """Кнопки редактируют текущее сообщение панели, команды присылают новое."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                await target.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup, disable_web_page_preview=True)


# ==================== ГЛАВНОЕ МЕНЮ ====================

async def main_screen(target: Message | CallbackQuery):
    group_id = get_group_id()
    group = f"<code>{group_id}</code>" if group_id else "❗ не задана"
    text = (
        "<b>🛠 Админ-панель</b>\n\n"
        f"👥 Рабочая группа: {group}\n"
        f"👮 Админов: {len(db.get_admins()) + 1}\n"
        f"🚫 Заблокировано: {len(db.get_banned())}\n\n"
        "Ваши сообщения боту в группу не пересылаются."
    )
    kb = InlineKeyboardBuilder()
    btn(kb, "👮 Администраторы", "admins")
    btn(kb, "🚫 Блокировки", "bans")
    btn(kb, "📝 Тексты", "texts")
    btn(kb, "👥 Рабочая группа", "group")
    btn(kb, "❓ Как работает бот", "help")
    kb.adjust(2, 2, 1)
    await show(target, text, kb.as_markup())


@router.message(or_f(CommandStart(), Command("admin")))
async def open_panel(message: Message, state: FSMContext):
    await state.clear()
    await main_screen(message)


@router.callback_query(Panel.filter(F.action == "main"))
async def cb_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await main_screen(call)


@router.callback_query(Panel.filter(F.action == "cancel"))
async def cb_cancel(call: CallbackQuery, callback_data: Panel, state: FSMContext):
    await state.clear()
    screens = {"admins": admins_screen, "bans": bans_screen, "group": group_screen}
    if callback_data.arg.startswith("text-"):
        await text_screen(call, callback_data.arg[5:])
    else:
        await screens.get(callback_data.arg, main_screen)(call)


HOW_IT_WORKS = """<b>❓ Как работает бот</b>

<b>Клиент</b>
• Пишет боту (включая /start) — бот пересылает сообщение в рабочую группу.
• Принимается только текст. Действует антифлуд: пауза между сообщениями, лимит в минуту с временным мутом, повторы одного текста игнорируются.
• Заблокированный клиент писать не может.

<b>Рабочая группа</b>
• Любой участник отвечает <b>реплаем</b> на пересланное сообщение — ответ уходит клиенту в виде «Ответ от @username» (или ID, если username нет).
• Отвечать можно сколько угодно раз, любыми сообщениями, без лимитов. После доставки бот ставит 👍.
• Админы бота могут там же ответить на сообщение клиента /ban или /unban.

<b>Админы</b>
• /start или /admin открывает эту панель. Сообщения админов в группу не пересылаются.
• 👮 Администраторы — список с именами; супер-админ добавляет и удаляет.
• 🚫 Блокировки — список заблокированных, разблокировка, блокировка по ID.
• 📝 Тексты — все сообщения бота можно изменить или отключить.
• 👥 Рабочая группа — проверить или сменить группу.

"""


@router.callback_query(Panel.filter(F.action == "help"))
async def cb_help(call: CallbackQuery):
    await show(call, HOW_IT_WORKS + HELP, back_kb())


# ==================== АДМИНИСТРАТОРЫ ====================

async def admins_screen(target: Message | CallbackQuery):
    user = target.from_user
    text = await admins_text(target.bot)
    kb = InlineKeyboardBuilder()
    if is_super_admin(user.id):
        for admin in await refreshed_admins(target.bot):
            label = admin["name"] or (f"@{admin['username']}" if admin["username"] else str(admin["admin_id"]))
            btn(kb, f"❌ Удалить: {label}", "admin_del", str(admin["admin_id"]))
        btn(kb, "➕ Добавить админа", "admin_add")
        text += "\n\nНажмите на админа, чтобы удалить его."
    else:
        text += "\n\nДобавлять и удалять админов может только супер-админ 👑."
    btn(kb, "⬅️ Назад", "main")
    kb.adjust(1)
    await show(target, text, kb.as_markup())


@router.callback_query(Panel.filter(F.action == "admins"))
async def cb_admins(call: CallbackQuery):
    await admins_screen(call)


@router.callback_query(Panel.filter(F.action == "admin_del"))
async def cb_admin_del(call: CallbackQuery, callback_data: Panel):
    if not is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    admin = db.get_admin(int(callback_data.arg))
    db.remove_admin(int(callback_data.arg))
    name = (admin or {}).get("name") or callback_data.arg
    await call.answer(f"Удалён: {name}")
    await admins_screen(call)


@router.callback_query(Panel.filter(F.action == "admin_add"))
async def cb_admin_add(call: CallbackQuery, state: FSMContext):
    if not is_super_admin(call.from_user.id):
        await call.answer("Только супер-админ", show_alert=True)
        return
    await state.set_state(Input.admin_add)
    await show(
        call,
        "➕ <b>Новый админ</b>\n\nОтправьте его <b>ID</b> или <b>перешлите сюда любое его сообщение</b>.",
        cancel_kb("admins"),
    )


async def person_from_input(message: Message) -> Optional[tuple[int, str, str]]:
    """ID из текста или автор пересланного сообщения -> (ID, имя, username)."""
    origin = message.forward_origin
    if origin is not None:
        sender = getattr(origin, "sender_user", None)
        if sender is None:
            return None  # у человека скрыта пересылка
        return sender.id, sender.full_name, sender.username or ""
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        return None
    return (user_id, *await person_info(message.bot, user_id))


not_command = ~F.text.startswith("/")


@router.message(Input.admin_add, not_command)
async def input_admin_add(message: Message, state: FSMContext):
    person = await person_from_input(message)
    if person is None:
        await message.answer(
            "Не получилось определить человека. Отправьте числовой ID "
            "(если у человека скрыта пересылка, переслать не выйдет).",
            reply_markup=cancel_kb("admins"),
        )
        return
    await state.clear()
    if person[0] == SUPER_ADMIN_ID:
        await message.answer("ℹ️ Это супер-админ.")
    else:
        added = db.add_admin(*person)
        status = "✅ Добавлен админ" if added else "ℹ️ Уже админ"
        await message.answer(f"{status}: {format_person(*person)}")
    await admins_screen(message)


# ==================== БЛОКИРОВКИ ====================

async def bans_screen(target: Message | CallbackQuery):
    banned = db.get_banned()
    kb = InlineKeyboardBuilder()
    if banned:
        lines = []
        for user in banned[:50]:
            name = " ".join(filter(None, [user["first_name"], user["last_name"]]))
            lines.append("• " + format_person(user["user_id"], name, user["username"] or ""))
            btn(kb, f"✅ Разблокировать: {name or user['user_id']}", "unban", str(user["user_id"]))
        text = "<b>🚫 Заблокированные</b>\n\n" + "\n".join(lines)
        if len(banned) > 50:
            text += f"\n…и ещё {len(banned) - 50}. Разблокировать по ID: /unban ID"
    else:
        text = "<b>🚫 Заблокированные</b>\n\nНикто не заблокирован."
    text += "\n\nВ группе можно ответить на сообщение пользователя командой /ban или /unban."
    btn(kb, "➕ Заблокировать по ID", "ban_add")
    btn(kb, "⬅️ Назад", "main")
    kb.adjust(1)
    await show(target, text, kb.as_markup())


@router.callback_query(Panel.filter(F.action == "bans"))
async def cb_bans(call: CallbackQuery):
    await bans_screen(call)


@router.callback_query(Panel.filter(F.action == "unban"))
async def cb_unban(call: CallbackQuery, callback_data: Panel):
    db.set_banned(int(callback_data.arg), False)
    await call.answer("Разблокирован")
    await bans_screen(call)


@router.callback_query(Panel.filter(F.action == "ban_add"))
async def cb_ban_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(Input.ban_add)
    await show(
        call,
        "🚫 <b>Блокировка</b>\n\nОтправьте ID пользователя или перешлите сюда его сообщение.",
        cancel_kb("bans"),
    )


@router.message(Input.ban_add, not_command)
async def input_ban_add(message: Message, state: FSMContext):
    person = await person_from_input(message)
    if person is None:
        await message.answer("Не получилось определить пользователя. Отправьте числовой ID.",
                             reply_markup=cancel_kb("bans"))
        return
    await state.clear()
    db.set_banned(person[0], True)
    await message.answer(f"🚫 Заблокирован: {format_person(*person)}")
    await bans_screen(message)


# ==================== ТЕКСТЫ ====================

async def texts_screen(target: Message | CallbackQuery):
    kb = InlineKeyboardBuilder()
    for key in DEFAULTS:
        mark = "✏️ " if db.get_text(key) is not None else ""
        btn(kb, f"{mark}{key}", "text", key)
    btn(kb, "⬅️ Назад", "main")
    kb.adjust(2)
    text = (
        "<b>📝 Тексты бота</b>\n\n"
        "Выберите текст, чтобы посмотреть и изменить его. ✏️ — изменён вами.\n\n"
        + "\n".join(f"<b>{key}</b> — {html.escape(info['description'])}" for key, info in DEFAULTS.items())
    )
    await show(target, text, kb.as_markup())


async def text_screen(target: Message | CallbackQuery, key: str):
    raw = get_raw(key)
    status = "изменён" if db.get_text(key) is not None else "по умолчанию"
    text = (
        f"<b>📝 {key}</b> ({status})\n{html.escape(DEFAULTS[key]['description'])}\n\n"
        f"<b>Как выглядит:</b>\n{render(key) or '<i>(отключён — не отправляется)</i>'}"
    )
    kb = InlineKeyboardBuilder()
    btn(kb, "✏️ Изменить", "text_edit", key)
    btn(kb, "↩️ По умолчанию", "text_reset", key)
    btn(kb, "⬅️ Назад", "texts")
    kb.adjust(2, 1)
    try:
        await show(target, text, kb.as_markup())
    except TelegramBadRequest:
        # Шаблон с плейсхолдерами может не пройти как HTML — показываем исходник
        await show(target, text.split("<b>Как выглядит:</b>")[0] + f"<pre>{html.escape(raw)}</pre>", kb.as_markup())


@router.callback_query(Panel.filter(F.action == "texts"))
async def cb_texts(call: CallbackQuery):
    await texts_screen(call)


@router.callback_query(Panel.filter(F.action == "text"))
async def cb_text(call: CallbackQuery, callback_data: Panel):
    if callback_data.arg in DEFAULTS:
        await text_screen(call, callback_data.arg)


@router.callback_query(Panel.filter(F.action == "text_reset"))
async def cb_text_reset(call: CallbackQuery, callback_data: Panel):
    db.reset_text(callback_data.arg)
    await call.answer("Сброшено по умолчанию")
    await text_screen(call, callback_data.arg)


@router.callback_query(Panel.filter(F.action == "text_edit"))
async def cb_text_edit(call: CallbackQuery, callback_data: Panel, state: FSMContext):
    key = callback_data.arg
    await state.set_state(Input.text_edit)
    await state.update_data(key=key)
    hint = "" if key == "reply" else "\nОтправьте «-», чтобы это сообщение не отправлялось."
    await show(
        call,
        f"✏️ <b>{key}</b>\n{html.escape(DEFAULTS[key]['description'])}\n\n"
        "Отправьте новый текст одним сообщением. Жирный, курсив, ссылки и премиум-эмодзи сохранятся."
        + hint + f"\n\n<b>Сейчас:</b>\n<pre>{html.escape(get_raw(key))}</pre>",
        cancel_kb(f"text-{key}"),
    )


@router.message(Input.text_edit, F.text, not_command)
async def input_text_edit(message: Message, state: FSMContext):
    key = (await state.get_data()).get("key")
    value = message.html_text.strip()
    if key == "reply" and value == DISABLED:
        await message.answer("❌ Текст ответа нельзя отключить. Отправьте другой текст.",
                             reply_markup=cancel_kb(f"text-{key}"))
        return
    await state.clear()
    db.set_text(key, value)
    await message.answer(f"✅ Текст <b>{key}</b> сохранён.")
    await text_screen(message, key)


# ==================== РАБОЧАЯ ГРУППА ====================

async def group_screen(target: Message | CallbackQuery):
    group_id = get_group_id()
    if group_id is None:
        status = "❗ Группа не задана — бот не сможет принимать заявки."
    else:
        try:
            chat = await target.bot.get_chat(group_id)
            status = f"✅ <b>{html.escape(chat.title or '')}</b> (<code>{group_id}</code>) — бот на месте"
        except Exception as e:
            status = f"❌ <code>{group_id}</code> — бот не видит группу: {html.escape(str(e))}"
    text = (
        "<b>👥 Рабочая группа</b>\n\n"
        f"{status}\n\n"
        "Сюда бот пересылает сообщения клиентов. Любой участник группы может ответить "
        "реплаем — ответ уйдёт клиенту.\n\n"
        "<b>Как сменить:</b> добавьте бота в новую группу и напишите там /setgroup "
        "(от имени админа бота) — или нажмите кнопку ниже и отправьте ID группы."
    )
    kb = InlineKeyboardBuilder()
    btn(kb, "✏️ Указать ID группы", "group_set")
    btn(kb, "⬅️ Назад", "main")
    kb.adjust(1)
    await show(target, text, kb.as_markup())


@router.callback_query(Panel.filter(F.action == "group"))
async def cb_group(call: CallbackQuery):
    await group_screen(call)


@router.callback_query(Panel.filter(F.action == "group_set"))
async def cb_group_set(call: CallbackQuery, state: FSMContext):
    await state.set_state(Input.group_set)
    await show(call, "Отправьте ID группы, например <code>-1001234567890</code>.", cancel_kb("group"))


@router.message(Input.group_set, not_command)
async def input_group_set(message: Message, state: FSMContext):
    try:
        group_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Нужен числовой ID, например <code>-1001234567890</code>.",
                             reply_markup=cancel_kb("group"))
        return
    await state.clear()
    await message.answer(await apply_group(message.bot, group_id))
    await group_screen(message)
