"""Кнопки Да/Нет под сообщением о переключении основного бота: разослать ли зеркалам новый адрес.
Только владельцам (OWNER_IDS)."""
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
import logging

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ManagedBotUpdated

from ..app import App
from ..mirrors import MirrorError
from ..notify import default_text

log = logging.getLogger(__name__)

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


@router.managed_bot()
async def on_managed_bot(update: ManagedBotUpdated, app: App) -> None:
    """Человек создал бота по кнопке «Создать автоматически»: забираем токен и подключаем как зеркало."""
    user, new_bot = update.user, update.bot_user
    mirrors = app.mirrors
    if mirrors is None or not mirrors.enabled or not mirrors.visible_for(user.id):
        return
    if await app.db.fetchval("SELECT 1 FROM mirrors WHERE bot_id = ?", (new_bot.id,)):
        return  # то же событие при изменении бота
    try:
        token = await app.main_bot.get_managed_bot_token(new_bot.id)
        mirror = await mirrors.add(token, owner_id=user.id, replacing=mirrors.owns(user.id))
    except MirrorError as e:
        text = f"❌ Зеркало не подключено: {e}"
        kb = None
    except Exception:  # noqa: BLE001
        log.exception("Автосоздание зеркала не удалось")
        text = "❌ Не получилось подключить зеркало. Попробуйте ещё раз или создайте бота вручную."
        kb = None
    else:
        text = f"✅ Зеркало <b>@{mirror.username}</b> создано и работает. Откройте его и нажмите /start."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=f"↗️ Открыть @{mirror.username}", url=f"https://t.me/{mirror.username}")]])
    try:
        await app.main_bot.send_message(user.id, text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        log.warning("Не удалось сообщить %s о создании зеркала", user.id)
