"""Связь защиты с aiogram: разбор сообщения и отправка."""
from __future__ import annotations

from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from ..moderation.telegram import undo_keyboard
from .detector import MessageView

# За один запрос Telegram удаляет не больше 100 сообщений.
_DELETE_BATCH = 100


def view_of(message: Message) -> MessageView:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or ()
    links: list[str] = []
    mentions = 0
    for entity in entities:
        kind = entity.type
        if kind == "url":
            links.append(entity.extract_from(text))
        elif kind == "text_link" and entity.url:
            links.append(entity.url)
        elif kind in ("mention", "text_mention"):
            mentions += 1
    # Кнопка-ссылка под пересланным сообщением тоже реклама.
    markup = message.reply_markup
    if markup is not None:
        for row in markup.inline_keyboard:
            links.extend(button.url for button in row if button.url)
    origin = message.forward_origin
    return MessageView(
        text=text,
        links=tuple(links),
        mentions=mentions,
        is_channel_forward=origin is not None and origin.type == "channel",
    )


class AiogramMessenger:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def delete_many(self, chat_id: int, message_ids: tuple[int, ...]) -> None:
        for start in range(0, len(message_ids), _DELETE_BATCH):
            with suppress(TelegramAPIError):
                await self._bot.delete_messages(
                    chat_id, list(message_ids[start : start + _DELETE_BATCH])
                )

    async def send(
        self, chat_id: int, thread_id: int | None, text: str, undo_id: int | None = None
    ) -> int | None:
        keyboard = undo_keyboard(undo_id) if undo_id is not None else None
        try:
            sent = await self._bot.send_message(
                chat_id, text, message_thread_id=thread_id, reply_markup=keyboard
            )
        except TelegramAPIError:
            return None
        return sent.message_id

    async def delete(self, chat_id: int, message_id: int) -> None:
        with suppress(TelegramAPIError):
            await self._bot.delete_message(chat_id, message_id)
