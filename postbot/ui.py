"""Кнопки и экраны. В личке у человека всегда один экран бота (и над ним пост, если он открыт):
новый экран заменяет старый, его сообщения и сообщения человека удаляются, чат остаётся чистым."""
import logging
from typing import Callable, Sequence, TypeVar

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup,
)

from .app import App

log = logging.getLogger(__name__)
T = TypeVar("T")

MAIN_CREATE = "📝 Создать пост"
MAIN_HISTORY = "🗂 История постов"
MAIN_ADMIN = "⚙️ Админка"

STYLES = {None: "⚪️ Обычная", "primary": "🔵 Синяя", "success": "🟢 Зелёная", "danger": "🔴 Красная"}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MAIN_CREATE, style="primary")],
                  [KeyboardButton(text=MAIN_HISTORY), KeyboardButton(text=MAIN_ADMIN)]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="Выберите действие")


def b(text: str, cb: str | None = None, style: str | None = None, icon: str | None = None,
      url: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text or "⠀", callback_data=cb if url is None else None, url=url,
                                style=style or None, icon_custom_emoji_id=icon or None)


def kb(rows: Sequence[Sequence[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    rows = [list(r) for r in rows if r]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def cancel_kb(cb: str, text: str = "❌ Отмена") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[b(text, cb, "danger")]])


def paginate(items: Sequence[T], page: int, size: int) -> tuple[Sequence[T], int, int]:
    pages = max(1, -(-len(items) // size))
    page = min(max(page, 0), pages - 1)
    return items[page * size:(page + 1) * size], page, pages


def nav_row(page: int, pages: int, make_cb: Callable[[int], str]) -> list[InlineKeyboardButton]:
    if pages <= 1:
        return []
    row = [b("⬅️", make_cb(page - 1))] if page > 0 else []
    row.append(b(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        row.append(b("➡️", make_cb(page + 1)))
    return row


def mark(flag: bool | int) -> str:
    return "✅" if flag else "☑️"


async def safe_delete(app: App, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await app.bot.delete_message(chat_id, message_id)
    except TelegramAPIError:
        pass  # уже удалено или слишком старое


async def drop_input(message: Message) -> None:
    """Убираем сообщение человека, чтобы в чате не копился мусор."""
    try:
        await message.delete()
    except TelegramAPIError:
        pass


async def show(app: App, user_id: int, text: str, markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
               *, edit: Message | None = None, keep_preview: bool = False) -> Message | None:
    """Показать экран. edit - нажатое сообщение: если это текущая панель, правим её на месте."""
    scr = app.screens.setdefault(user_id, {})
    panel = scr.get("panel")
    inline = markup is None or isinstance(markup, InlineKeyboardMarkup)
    if (edit is not None and edit.message_id == panel and inline and not scr.get("reply")
            and edit.text is not None):
        try:
            res = await app.bot.edit_message_text(text=text, chat_id=user_id, message_id=edit.message_id,
                                                  reply_markup=markup)
            if not keep_preview:
                await safe_delete(app, user_id, scr.pop("preview", None))
            return res if isinstance(res, Message) else None
        except TelegramBadRequest as e:
            if "not modified" in e.message:
                if not keep_preview:
                    await safe_delete(app, user_id, scr.pop("preview", None))
                return edit
            log.debug("Не вышло поправить экран: %s", e.message)

    msg = await app.bot.send_message(user_id, text, reply_markup=markup)
    scr["panel"] = msg.message_id
    scr["reply"] = not inline
    await safe_delete(app, user_id, panel)
    if edit is not None and edit.message_id not in (panel, scr.get("preview"), msg.message_id):
        await safe_delete(app, user_id, edit.message_id)
    if not keep_preview:
        await safe_delete(app, user_id, scr.pop("preview", None))
    return msg


async def clear(app: App, user_id: int) -> None:
    scr = app.screens.pop(user_id, {})
    for key in ("preview", "panel"):
        await safe_delete(app, user_id, scr.get(key))
