"""Проверка, показывает ли Telegram премиум-эмодзи от этого бота.
Бот шлёт себе в чат короткое сообщение с премиум-эмодзи и смотрит, что вернулось."""
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..app import App

HOW_TO_FIX = (
    "Telegram показывает премиум-эмодзи от бота, только если у аккаунта, который создал бота в @BotFather, "
    "есть Telegram Premium, либо у бота куплен юзернейм на Fragment. Сохранённые эмодзи никуда не денутся, "
    "как только условие выполнится, они сразу появятся."
)


async def check(app: App, chat_id: int, emoji_id: str) -> str:
    try:
        msg = await app.bot.send_message(
            chat_id, f'<tg-emoji emoji-id="{emoji_id}">👍</tg-emoji> проверка премиум-эмодзи',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="проверка", icon_custom_emoji_id=emoji_id, callback_data="noop")]]),
            disable_notification=True)
    except TelegramAPIError as e:
        return f"Не получилось проверить: {e.message}"
    text_ok = any(e.type == "custom_emoji" for e in (msg.entities or []))
    kb = msg.reply_markup.inline_keyboard if msg.reply_markup else []
    button_ok = bool(kb and kb[0] and kb[0][0].icon_custom_emoji_id)
    try:
        await msg.delete()
    except TelegramAPIError:
        pass
    if text_ok and button_ok:
        return "✨ Всё работает: премиум-эмодзи видно и в тексте, и на кнопках."
    where = " и ".join(w for w, ok in (("в тексте", text_ok), ("на кнопках", button_ok)) if not ok)
    return f"⚠️ Telegram убирает премиум-эмодзи {where}.\n\n{HOW_TO_FIX}"


def lost_emoji(html: str, sent: Message) -> bool:
    """В тексте были премиум-эмодзи, а Telegram вернул сообщение без них."""
    if "<tg-emoji" not in html:
        return False
    entities = list(sent.entities or []) + list(sent.caption_entities or [])
    return not any(e.type == "custom_emoji" for e in entities)
