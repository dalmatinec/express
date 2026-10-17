"""Как пост превращается в сообщение Telegram: отправка, правка, кнопки."""
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CopyTextButton, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAnimation,
    InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, Message,
)

from ..app import App
from ..models import Post, PostButton

log = logging.getLogger(__name__)

BLANK = "⠀"  # пустой текст Telegram не принимает
WITH_EXTRAS = ("photo", "video", "animation")  # у них есть спойлер и подпись над медиа


class RenderError(Exception):
    """Ошибка, которую можно показать человеку как есть."""


def post_button(b: PostButton) -> InlineKeyboardButton:
    common = dict(text=b.label or BLANK, icon_custom_emoji_id=b.icon or None, style=b.style or None)
    if b.kind == "copy":
        return InlineKeyboardButton(**common, copy_text=CopyTextButton(text=b.value or b.label or "-"))
    if b.kind == "popup":
        return InlineKeyboardButton(**common, callback_data=f"pp:{b.token}")
    return InlineKeyboardButton(**common, url=b.value)


def post_markup(buttons: list[PostButton]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    current = None
    for b in buttons:
        if b.row != current or not rows:
            rows.append([])
            current = b.row
        rows[-1].append(post_button(b))
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def preview_options(post: Post) -> LinkPreviewOptions:
    if post.preview == "off":
        return LinkPreviewOptions(is_disabled=True)
    return LinkPreviewOptions(
        is_disabled=False,
        prefer_small_media=True if post.preview == "small" else None,
        prefer_large_media=True if post.preview == "large" else None,
        show_above_text=True if post.preview_above else None,
    )


async def send_post(app: App, chat_id: int, post: Post, buttons: list[PostButton], *,
                    preview: bool = False) -> Message:
    """Отправить пост. preview=True - показ в личке: без звука, без запрета копирования."""
    bot = app.bot
    markup = post_markup(buttons)
    html = post.html or BLANK
    flags = dict(disable_notification=True if preview else bool(post.silent),
                 protect_content=False if preview else bool(post.protect))
    info = await app.media.get(post.media_id)
    if info is None:
        return await bot.send_message(chat_id, html, reply_markup=markup, link_preview_options=preview_options(post),
                                      **flags)

    send = {"photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
            "audio": bot.send_audio, "document": bot.send_document}[info.kind]
    extras = dict(flags)
    if info.kind in WITH_EXTRAS:
        extras.update(has_spoiler=bool(post.spoiler), show_caption_above_media=bool(post.caption_above))

    for force_disk in (False, True):
        src = await app.media.source(info.id, force_disk=force_disk)
        if src is None:
            break
        try:
            msg = await send(chat_id, src, caption=html, reply_markup=markup, **extras)
        except TelegramBadRequest as e:
            if not force_disk and "file" in e.message.lower():
                await app.media.forget_file_id(info.id)  # старый file_id, пробуем залить с диска
                continue
            raise
        if isinstance(src, FSInputFile):
            fid = _file_id(msg)
            if fid:
                await app.media.remember(info.id, fid)
        return msg
    raise RenderError("Медиа поста недоступно: нет ни file_id, ни файла на диске. Загрузите медиа заново.")


def _file_id(msg: Message) -> str | None:
    if msg.photo:
        return msg.photo[-1].file_id
    for attr in ("animation", "video", "audio", "document"):
        obj = getattr(msg, attr, None)
        if obj is not None:
            return obj.file_id
    return None


async def edit_post(app: App, chat_id: int, message_id: int, post: Post, buttons: list[PostButton],
                    was_media: bool) -> None:
    """Обновить уже опубликованный пост. was_media - было ли в сообщении медиа."""
    bot = app.bot
    markup = post_markup(buttons)
    html = post.html or BLANK
    info = await app.media.get(post.media_id)
    if (info is not None) != was_media:
        raise RenderError("пост поменялся с текста на медиа или наоборот, Telegram так не правит. "
                          "Удалите старый и опубликуйте заново.")
    try:
        if info is None:
            await bot.edit_message_text(text=html, chat_id=chat_id, message_id=message_id, reply_markup=markup,
                                        link_preview_options=preview_options(post))
            return
        src = await app.media.source(info.id)
        if src is None:
            raise RenderError("медиа поста недоступно")
        cls = {"photo": InputMediaPhoto, "video": InputMediaVideo, "animation": InputMediaAnimation,
               "audio": InputMediaAudio, "document": InputMediaDocument}[info.kind]
        extras = {}
        if info.kind in WITH_EXTRAS:
            extras = dict(has_spoiler=bool(post.spoiler), show_caption_above_media=bool(post.caption_above))
        msg = await bot.edit_message_media(media=cls(media=src, caption=html, parse_mode="HTML", **extras),
                                           chat_id=chat_id, message_id=message_id, reply_markup=markup)
        if isinstance(src, FSInputFile) and isinstance(msg, Message):
            fid = _file_id(msg)
            if fid:
                await app.media.remember(info.id, fid)
    except TelegramBadRequest as e:
        if "not modified" in e.message:
            return
        raise


async def edit_markup(app: App, chat_id: int, message_id: int, buttons: list[PostButton]) -> None:
    try:
        await app.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id,
                                                reply_markup=post_markup(buttons))
    except TelegramBadRequest as e:
        if "not modified" not in e.message:
            raise
