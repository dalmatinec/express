"""Старт, главное меню, общие мелочи."""
from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..app import App
from ..utils.text import h

router = Router(name="common")
router.message.filter(F.chat.type == "private")


def clicked(cq: CallbackQuery) -> Message | None:
    return cq.message if isinstance(cq.message, Message) else None


def parts(cq: CallbackQuery) -> list[str]:
    return (cq.data or "").split(":")


async def home(app: App, user_id: int, note: str = "", name: str = "") -> None:
    """Главный экран с нижней клавиатурой."""
    greet = f"👋 Привет, {h(name)}!\n\n" if name else ""
    text = (f"{note}\n\n" if note else "") + greet + (
        "Это конструктор постов для ваших каналов.\n"
        f"{ui.MAIN_CREATE} - собрать новый пост с кнопками и опубликовать.\n"
        f"{ui.MAIN_HISTORY} - все посты: повторить, изменить, удалить.\n"
        f"{ui.MAIN_ADMIN} - каналы, админы, бэкап.")
    await ui.show(app, user_id, text, ui.main_keyboard())


@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext, app: App) -> None:
    await state.clear()
    await ui.drop_input(message)
    await home(app, message.from_user.id, name=message.from_user.first_name or "")


@router.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext, app: App) -> None:
    await state.clear()
    await ui.drop_input(message)
    await home(app, message.from_user.id, note="Отменено.")


@router.message(F.text == ui.MAIN_CREATE, StateFilter("*"))
async def menu_create(message: Message, state: FSMContext, app: App) -> None:
    from . import editor
    await ui.drop_input(message)
    await editor.start_new(app, message.from_user.id, state)


@router.message(F.text == ui.MAIN_HISTORY, StateFilter("*"))
async def menu_history(message: Message, state: FSMContext, app: App) -> None:
    from . import history
    await state.clear()
    await ui.drop_input(message)
    await history.show_list(app, message.from_user.id, 0)


@router.message(F.text == ui.MAIN_ADMIN, StateFilter("*"))
async def menu_admin(message: Message, state: FSMContext, app: App) -> None:
    from . import admin
    await state.clear()
    await ui.drop_input(message)
    await admin.show_main(app, message.from_user.id)


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()


@router.callback_query(F.data == "m:home", StateFilter("*"))
async def cb_home(cq: CallbackQuery, state: FSMContext, app: App) -> None:
    await state.clear()
    await cq.answer()
    await home(app, cq.from_user.id)
