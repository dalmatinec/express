"""Пользовательская часть. Всё строится из кэша в памяти; база - только при выдаче ссылки.

Callback-данные:
  m         главное меню
  i:<id>    нажали кнопку-ссылку: ссылка приходит отдельным сообщением
  cl:<id>   закрыть сообщение со ссылкой
  mir       создать своё зеркало (бот присылает токен, зеркало запускается сразу)

Роутер собирается функцией build_router: основной бот и зеркала работают на отдельных диспетчерах,
а один Router нельзя подключить к двум.
"""
import math

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, User

from ..app import App
from ..mirrors import MirrorError
from ..richtext import html_to_plain
from ..services.links import LinkBusy, LinkUnavailable
from ..store import Item, now
from ..ui import current_of, fill, markup, menu_rows, minutes_text, safe_delete, show, sys_button

class MirrorFlow(StatesGroup):
    token = State()


def home(app: App, user: User) -> tuple[str, int | None, object]:
    store = app.store
    text = store.text("start")
    html = fill(text.html, user=user)
    rows = menu_rows(store)
    if not rows:
        html = f"{html}\n\n{store.text('empty_menu').html}".strip()
    if app.mirrors is not None and app.mirrors.public:
        rows = [*rows, [InlineKeyboardButton(text="🪞 Создать зеркало", callback_data="mir")]]
    return html, text.media_id, markup(rows)


async def cmd_start(message: Message, command: CommandObject, state: FSMContext, app: App) -> None:
    await state.set_state(None)
    user, chat_id = message.from_user, message.chat.id
    if app.store.setting("clean_chat", 1):
        await safe_delete(app, chat_id, message.message_id)
    html, media_id, kb = home(app, user)
    await show(app, user.id, chat_id, html, media_id, kb)
    payload = command.args or ""
    if payload.startswith("i") and payload[1:].isdigit():  # прямая ссылка на кнопку: t.me/bot?start=i5
        await open_item(app, user, chat_id, app.store.items.get(int(payload[1:])), None)
        return
    await app.ads.show(chat_id, user.id, "start")


async def cb_home(call: CallbackQuery, state: FSMContext, app: App) -> None:
    await call.answer()
    await state.set_state(None)
    html, media_id, kb = home(app, call.from_user)
    await show(app, call.from_user.id, call.message.chat.id, html, media_id, kb, current=current_of(call.message))


async def cb_item(call: CallbackQuery, app: App) -> None:
    item = app.store.items.get(int(call.data.split(":")[1]))
    await open_item(app, call.from_user, call.message.chat.id, item, call)


async def cb_close(call: CallbackQuery, app: App) -> None:
    """Закрыть сообщение со ссылкой. Главное меню остаётся."""
    app.link_msgs.pop((call.from_user.id, int(call.data.split(":")[1])), None)
    await call.answer()
    await safe_delete(app, call.message.chat.id, call.message.message_id)


async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


async def open_item(app: App, user: User, chat_id: int, item: Item | None, call: CallbackQuery | None) -> None:
    """Каждая кнопка присылает ссылку отдельным сообщением с кнопкой Закрыть. Меню остаётся на месте."""
    store = app.store

    async def popup(key: str | None = None) -> None:
        if call is not None:
            text = html_to_plain(store.text(key).html)[:190] if key else None
            await call.answer(text, show_alert=bool(key) and key != "link_sent")

    if item is None or not item.is_active or item.kind != "invite" or not store.item_ready(item):
        return await popup("unavailable")
    try:
        issued = await app.links.issue(item, user.id)
    except LinkBusy:
        return await popup("busy")
    except LinkUnavailable as e:
        await popup("unavailable")
        await app.links.chat_failed(item.chat_id or 0, str(e))
        return

    key = (user.id, item.id)
    sent = app.link_msgs.get(key)
    if sent is not None:
        if sent[1] == issued.link:  # эта ссылка уже лежит в чате: второй раз не шлём
            return await popup("link_sent")
        await safe_delete(app, chat_id, sent[0])  # прошлая ссылка использована или истекла
    await popup()

    text = store.text("link_request" if item.mode == "request" else "link_one_time")
    html = fill(text.html, user=user, button=item.label)
    if issued.expires_at:
        left = max(1, math.ceil((issued.expires_at - now()) / 60))
        html += "\n\n" + fill(store.text("link_ttl").html, time=minutes_text(left))
    kb = markup([[sys_button(store, "join", url=issued.link)], [sys_button(store, "close", cb=f"cl:{item.id}")]])
    msg = await send_link(app, user.id, chat_id, html, text.media_id, kb)
    if msg is not None:
        app.remember_link_msg(key, msg.message_id, issued.link)
    await app.ads.show(chat_id, user.id, "link")


async def send_link(app: App, user_id: int, chat_id: int, html: str, media_id: int | None, kb) -> Message | None:
    protect = bool(app.store.setting("protect_content", 0)) and app.perms(user_id) is None
    try:
        msg = None
        if app.media.get(media_id):
            msg = await app.media.send(app.bot, chat_id, media_id, html, kb, protect_content=protect)
        if msg is None:
            msg = await app.bot.send_message(chat_id, html, reply_markup=kb, protect_content=protect)
        return msg
    except TelegramForbiddenError:
        await app.mark_blocked(user_id)
    except TelegramAPIError:
        pass
    return None


async def any_message(message: Message, app: App) -> None:
    """Случайные сообщения не засоряют чат."""
    if app.store.setting("clean_chat", 1):
        await safe_delete(app, message.chat.id, message.message_id)


# ---------- зеркала ----------
MIRROR_HELP = (
    "🪞 <b>Зеркало</b>\n\n"
    "Это ваш собственный бот с теми же кнопками и ссылками. Если основной бот заблокируют, "
    "зеркало продолжит работать, а мы сообщим вам новый адрес.\n\n"
    "1. Откройте @BotFather → /newbot и создайте бота.\n"
    "2. Пришлите сюда его токен. Сообщение с токеном я сразу удалю."
)


async def cb_mirror(call: CallbackQuery, state: FSMContext, app: App) -> None:
    await call.answer()
    if app.mirrors is None or not app.mirrors.public:
        return
    await state.set_state(MirrorFlow.token)
    kb = markup([[InlineKeyboardButton(text="‹ Назад", callback_data="m")]])
    await show(app, call.from_user.id, call.message.chat.id, MIRROR_HELP, None, kb, current=current_of(call.message))


async def in_mirror_token(message: Message, state: FSMContext, app: App) -> None:
    user, chat_id = message.from_user, message.chat.id
    token = (message.text or "").strip()
    await safe_delete(app, chat_id, message.message_id)  # токен в чате не оставляем
    back = markup([[InlineKeyboardButton(text="‹ В меню", callback_data="m")]])
    if app.mirrors is None or not app.mirrors.public:
        await state.set_state(None)
        return
    try:
        mirror = await app.mirrors.add(token, owner_id=user.id)
    except MirrorError as e:
        await show(app, user.id, chat_id, f"❌ {e}\n\nПришлите токен ещё раз или вернитесь в меню.", None, back)
        return
    await state.set_state(None)
    await show(app, user.id, chat_id, f"✅ Зеркало <b>@{mirror.username}</b> запущено. Откройте его и нажмите /start.",
               None, back)


def build_router(name: str = "user") -> Router:
    r = Router(name=name)
    r.message.filter(F.chat.type == "private")
    r.message.register(cmd_start, CommandStart())
    r.callback_query.register(cb_home, F.data == "m")
    r.callback_query.register(cb_item, F.data.regexp(r"^i:\d+$"))
    r.callback_query.register(cb_close, F.data.regexp(r"^cl:\d+$"))
    r.callback_query.register(cb_noop, F.data == "noop")
    r.callback_query.register(cb_mirror, F.data == "mir")
    r.message.register(in_mirror_token, MirrorFlow.token, F.text)
    r.message.register(any_message)
    return r


router = build_router()
