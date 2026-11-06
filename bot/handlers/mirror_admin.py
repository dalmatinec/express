"""Кнопки Да/Нет под сообщением о переключении основного бота: разослать ли зеркалам новый адрес.
Только владельцам (OWNER_IDS)."""
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from ..app import App
from ..notify import default_text

router = Router(name="mirror-admin")


@router.callback_query(F.data.startswith("mn:"))
async def cb_notify(call: CallbackQuery, app: App) -> None:
    if call.from_user.id not in app.config.owner_ids:
        await call.answer("Только для владельца.", show_alert=True)
        return
    action = call.data.split(":")[1]
    if action == "y":
        started = app.notifier.start(default_text(app.bot_username))
        text = "📣 Отправляю уведомление зеркалам." if started else "Рассылка уже идёт."
    else:
        text = "Хорошо, ничего не отправляю."
    await call.answer()
    try:
        await call.message.edit_text(text)  # type: ignore[union-attr]
    except (TelegramAPIError, AttributeError):
        pass
