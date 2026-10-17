"""Всё, что не подошло ни под один шаг: сообщения убираем, старые кнопки просто гасим."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from .. import ui

router = Router(name="fallback")


@router.message(F.chat.type == "private")
async def stray_message(message: Message) -> None:
    await ui.drop_input(message)


@router.callback_query()
async def stray_callback(cq: CallbackQuery) -> None:
    await cq.answer("Кнопка устарела, нажмите /start")
