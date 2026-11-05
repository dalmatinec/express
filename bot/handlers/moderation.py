"""Команды модерации в группах и кнопки на карточках."""
from __future__ import annotations

import logging
import secrets
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from ..admins import AdminRights
from ..cache import TTLCache
from ..db import queries as q
from ..moderation.cards import (
    mention,
    render_card,
    render_history,
    render_journal,
    render_lifted,
    undo_target,
)
from ..moderation.parser import ParsedArgs, parse_args
from ..moderation.service import ModerationError, ModSettings, Outcome, Person
from ..moderation.telegram import AiogramActions
from ..texts import t

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)

# Одна команда может иметь несколько написаний. Один фильтр на все варианты: он проверяется
# на каждом сообщении в каждом чате, поэтому не плодим отдельный обработчик на команду.
_ACTIONS = {
    "ban": ("ban", "бан", "tban"),
    "unban": ("unban", "разбан"),
    "mute": ("mute", "мут", "tmute"),
    "unmute": ("unmute", "размут"),
    "kick": ("kick", "кик"),
    "warn": ("warn", "варн", "пред"),
    "unwarn": ("unwarn", "разварн", "снятьпред"),
    "warns": ("warns", "варны", "преды"),
    "history": ("history", "история"),
    "journal": ("journal", "журнал"),
}
ALIASES = {alias: action for action, names in _ACTIONS.items() for alias in names}
WITH_DURATION = frozenset({"ban", "mute"})
NO_TARGET = frozenset({"journal"})
HISTORY_LIMIT = 10
JOURNAL_LIMIT = 15

UNDO_PREFIX = "mod:u:"
ANON_PREFIX = "mod:a:"


@dataclass(frozen=True, slots=True)
class PendingCommand:
    """Команда анонимного админа, ждёт нажатия кнопки."""

    action: str
    target: Person | None
    args: ParsedArgs
    command_message_id: int
    thread_id: int | None


# Живёт в памяти: после перезапуска кнопка просто попросит повторить команду.
_pending: TTLCache[str, PendingCommand] = TTLCache(1_000, 300.0)


def _person(user: User) -> Person:
    return Person(user.id, user.full_name)


def _undo_keyboard(punishment_id: int) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=t("btn_undo"), callback_data=f"{UNDO_PREFIX}{punishment_id}"
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


async def _find_target(
    message: Message, args: ParsedArgs, explicit: Person | None, app: App
) -> Person | None:
    if explicit is not None:
        return explicit
    if args.target is not None:
        if args.target.startswith("@"):
            return await _person_by_username(args.target[1:], app)
        user_id = int(args.target)
        row = await app.db.fetchrow(q.GET_USER, user_id)
        return Person(user_id, row["first_name"] if row and row["first_name"] else f"ID {user_id}")
    reply = message.reply_to_message
    # В форуме сообщение без ответа формально отвечает на создание темы: это не цель.
    if reply is None or reply.forum_topic_created is not None:
        return None
    if reply.sender_chat is not None:
        raise ModerationError("target_channel")
    return _person(reply.from_user) if reply.from_user else None


async def _person_by_username(username: str, app: App) -> Person:
    row = await app.db.fetchrow(q.FIND_USER_BY_USERNAME, username)
    if row is None:
        # Человек мог написать только что, а запись в базу идёт пачками.
        await app.stats.flush()
        row = await app.db.fetchrow(q.FIND_USER_BY_USERNAME, username)
    if row is None:
        raise ModerationError("unknown_username")
    return Person(row["id"], row["first_name"] or f"@{username}")


def _mention_target(message: Message) -> tuple[Person | None, str | None]:
    """Упоминание человека без username: цель лежит прямо в разметке сообщения."""
    text = message.text or message.caption or ""
    for entity in message.entities or []:
        if entity.type == "text_mention" and entity.user is not None:
            # Только если упоминание идёт первым аргументом, иначе это часть причины.
            head = entity.extract_from(text)
            before = text.split(maxsplit=1)
            if len(before) == 2 and before[1].startswith(head):
                return _person(entity.user), before[1][len(head):].strip()
    return None, None


async def _settings(app: App, bot_id: int, chat_id: int) -> ModSettings:
    info = await app.chats.get(bot_id, chat_id)
    return ModSettings.from_chat(info.settings if info else None)


async def _send(
    bot: Bot,
    chat_id: int,
    thread_id: int | None,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    await bot.send_message(chat_id, text, message_thread_id=thread_id, reply_markup=keyboard)


async def _delete(bot: Bot, chat_id: int, message_id: int) -> None:
    with suppress(TelegramAPIError):  # нет права удалять или сообщение уже удалено
        await bot.delete_message(chat_id, message_id)


async def _execute(
    bot: Bot,
    app: App,
    chat_id: int,
    action: str,
    target: Person | None,
    args: ParsedArgs,
    admin: Person,
    command_message_id: int,
    thread_id: int | None,
) -> None:
    """Выполняет проверенную команду. Ошибки ModerationError показывает вызывающий."""
    service = app.moderation
    settings = await _settings(app, bot.id, chat_id)
    offset = settings.utc_offset_hours
    tg = AiogramActions(bot)
    text: str
    keyboard: InlineKeyboardMarkup | None = None

    if action == "journal":
        rows = await service.repo.journal(bot.id, chat_id, JOURNAL_LIMIT)
        text = render_journal(rows, offset)
    elif action == "history":
        rows = await service.repo.history(bot.id, chat_id, target.id, HISTORY_LIMIT)
        text = render_history(target, rows, offset)
    elif action == "warns":
        count = await service.repo.count_warns(bot.id, chat_id, target.id)
        text = t("warns", target=mention(target), count=count, limit=settings.warn_limit)
    elif action in {"unban", "unmute", "unwarn"}:
        outcome: Outcome
        if action == "unwarn":
            outcome = await service.unwarn(tg, chat_id, target, admin)
        else:
            await service.check_bot(bot, chat_id)
            outcome = await service.lift(tg, chat_id, action[2:], target, admin)
        text = render_lifted(outcome)
    else:
        await service.check_target(bot, chat_id, admin.id, target.id)
        if action == "warn":
            outcome = await service.warn(tg, chat_id, target, admin, settings, reason=args.reason)
        else:
            await service.check_bot(bot, chat_id)
            outcome = await service.punish(
                tg, chat_id, action, target, admin, seconds=args.seconds, reason=args.reason
            )
        text = render_card(outcome, offset)
        undo_id = undo_target(outcome)
        if undo_id is not None:
            keyboard = _undo_keyboard(undo_id)

    await _send(bot, chat_id, thread_id, text, keyboard)
    if settings.delete_commands:
        await _delete(bot, chat_id, command_message_id)


def _forget_admins_if_stale(app: App, bot_id: int, chat_id: int, error: ModerationError) -> None:
    # Telegram знает состав админов точнее нашего кэша.
    if error.code in {"target_admin", "bot_no_rights"}:
        app.admins.invalidate(bot_id, chat_id)


async def on_command(message: Message, command: CommandObject, bot: Bot, app: App) -> None:
    action = ALIASES[command.command.lower()]
    chat_id = message.chat.id
    anonymous = message.sender_chat is not None and message.sender_chat.id == chat_id
    if message.sender_chat is not None and not anonymous:
        return  # пост от имени канала

    admin: Person | None = None
    if not anonymous:
        author = message.from_user
        if author is None:
            return
        rights: AdminRights | None = await app.moderation.admin_rights(bot, chat_id, author.id)
        if rights is None:
            return  # обычному участнику молчим, чтобы команду не использовали для шума
        if not rights.can_restrict:
            await message.reply(t("mod_err_no_rights"))
            return
        admin = _person(author)

    try:
        explicit, rest = _mention_target(message)
        raw_args = rest if explicit is not None else command.args
        args = parse_args(
            raw_args, with_duration=action in WITH_DURATION, has_target=explicit is not None
        )
        if args.error:
            raise ModerationError(args.error)
        target = None
        if action not in NO_TARGET:
            target = await _find_target(message, args, explicit, app)
            if target is None:
                await message.reply(t("usage_target"))
                return

        if anonymous:
            token = secrets.token_urlsafe(8)
            _pending.set(
                token,
                PendingCommand(action, target, args, message.message_id, message.message_thread_id),
            )
            button = InlineKeyboardButton(
                text=t("btn_confirm_anon"), callback_data=f"{ANON_PREFIX}{token}"
            )
            await message.reply(
                t("anon_prompt"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]])
            )
            return

        await _execute(
            bot, app, chat_id, action, target, args, admin,
            message.message_id, message.message_thread_id,
        )
    except ModerationError as error:
        _forget_admins_if_stale(app, bot.id, chat_id, error)
        await message.reply(t(f"mod_err_{error.code}"))


async def _callback_admin(
    callback: CallbackQuery, bot: Bot, app: App, chat_id: int
) -> Person | None:
    rights = await app.moderation.admin_rights(bot, chat_id, callback.from_user.id)
    if rights is None or not rights.can_restrict:
        await callback.answer(t("mod_err_no_rights"), show_alert=True)
        return None
    return _person(callback.from_user)


async def on_undo(callback: CallbackQuery, bot: Bot, app: App) -> None:
    message = callback.message
    if message is None or not isinstance(message, Message):
        await callback.answer(t("mod_err_expired"), show_alert=True)
        return
    chat_id = message.chat.id
    admin = await _callback_admin(callback, bot, app, chat_id)
    if admin is None:
        return
    punishment_id = int(callback.data.removeprefix(UNDO_PREFIX))
    try:
        await app.moderation.check_bot(bot, chat_id)
        outcome = await app.moderation.undo(
            AiogramActions(bot), chat_id, punishment_id, admin
        )
    except ModerationError as error:
        _forget_admins_if_stale(app, bot.id, chat_id, error)
        await callback.answer(t(f"mod_err_{error.code}"), show_alert=True)
        if error.code == "already":
            with suppress(TelegramAPIError):
                await message.edit_reply_markup(reply_markup=None)
        return
    with suppress(TelegramAPIError):
        await message.edit_text(render_lifted(outcome), reply_markup=None)
    await callback.answer(t("done"))


async def on_anonymous_confirm(callback: CallbackQuery, bot: Bot, app: App) -> None:
    message = callback.message
    if message is None or not isinstance(message, Message):
        await callback.answer(t("mod_err_expired"), show_alert=True)
        return
    chat_id = message.chat.id
    token = callback.data.removeprefix(ANON_PREFIX)
    pending = _pending.get(token)
    if pending is None:
        await callback.answer(t("mod_err_expired"), show_alert=True)
        await _delete(bot, chat_id, message.message_id)
        return
    admin = await _callback_admin(callback, bot, app, chat_id)
    if admin is None:
        return
    _pending.delete(token)
    try:
        await _execute(
            bot, app, chat_id, pending.action, pending.target, pending.args, admin,
            pending.command_message_id, pending.thread_id,
        )
    except ModerationError as error:
        _forget_admins_if_stale(app, bot.id, chat_id, error)
        await callback.answer(t(f"mod_err_{error.code}"), show_alert=True)
        await _delete(bot, chat_id, message.message_id)
        return
    await _delete(bot, chat_id, message.message_id)
    await callback.answer(t("done"))


def build_router() -> Router:
    router = Router(name="moderation")
    router.message.filter(F.chat.type.in_({"group", "supergroup"}))
    router.message.register(on_command, Command(*ALIASES, prefix="/!", ignore_case=True))
    router.callback_query.register(on_undo, F.data.startswith(UNDO_PREFIX))
    router.callback_query.register(on_anonymous_confirm, F.data.startswith(ANON_PREFIX))
    return router
