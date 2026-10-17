"""Фейковый Telegram Bot API для тестов: запоминает сообщения во всех чатах."""
import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery, DeleteMessage, DeleteWebhook, GetWebhookInfo, EditMessageMedia, EditMessageReplyMarkup, EditMessageText, GetChat,
    GetChatMember, GetFile, GetMe, LeaveChat, PinChatMessage, SendAnimation, SendAudio, SendDocument,
    SendMessage, SendPhoto, SendVideo, SetMyCommands, TelegramMethod,
)
from aiogram.types import (
    ChatFullInfo, ChatMemberAdministrator, ChatMemberLeft, File, InputFile, Message, User,
)

BOT_RIGHTS = dict(
    can_be_edited=False, is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
    can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False, can_change_info=False,
    can_invite_users=True, can_post_stories=False, can_edit_stories=False, can_delete_stories=False,
    can_send_welcome_messages=False, can_post_messages=True, can_edit_messages=True,
)


@dataclass
class ChatMessage:
    id: int
    chat_id: int
    text: str | None
    caption: str | None
    kind: str  # text photo animation video document audio
    markup: Any
    file_id: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class FakeTelegram(BaseSession):
    bot_id: int = 1000
    calls: list[TelegramMethod] = field(default_factory=list)
    messages: dict[tuple[int, int], ChatMessage] = field(default_factory=dict)
    uploads: int = 0
    download_data: bytes = b"\x89PNG fake"
    sent_files: list[bytes] = field(default_factory=list)
    chats: dict[int, dict] = field(default_factory=dict)   # id -> {title, type, username}
    no_rights: set[int] = field(default_factory=set)       # тут бот без права публиковать
    gone: set[int] = field(default_factory=set)            # отсюда бота выкинули
    premium_ok: bool = True
    pinned: list[tuple[int, int]] = field(default_factory=list)
    left: list[int] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        BaseSession.__init__(self)
        self._ids = itertools.count(100)
        self._fids = itertools.count(1)

    async def close(self) -> None:
        pass

    async def stream_content(self, url: str, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.download_data

    def chat_type(self, chat_id: int) -> str:
        return self.chats.get(chat_id, {}).get("type", "private" if chat_id > 0 else "channel")

    def msg(self, bot: Bot, m: ChatMessage) -> Message:
        data: dict[str, Any] = {"message_id": m.id, "date": 0,
                                "chat": {"id": m.chat_id, "type": self.chat_type(m.chat_id)}}
        if m.kind == "text":
            data["text"] = m.text
        else:
            data["caption"] = m.caption
            fid = m.file_id or "x"
            if m.kind == "photo":
                data["photo"] = [{"file_id": fid, "file_unique_id": fid, "width": 1, "height": 1}]
            elif m.kind in ("animation", "video"):
                data[m.kind] = {"file_id": fid, "file_unique_id": fid, "width": 1, "height": 1, "duration": 1}
            elif m.kind == "audio":
                data["audio"] = {"file_id": fid, "file_unique_id": fid, "duration": 1}
            else:
                data["document"] = {"file_id": fid, "file_unique_id": fid}
        body = m.text if m.kind == "text" else m.caption
        found = re.search(r'emoji-id="(\d+)"', body or "")
        if self.premium_ok and found:
            data["entities" if m.kind == "text" else "caption_entities"] = [
                {"type": "custom_emoji", "offset": 0, "length": 1, "custom_emoji_id": found[1]}]
        if m.markup is not None and hasattr(m.markup, "inline_keyboard"):
            dump = m.markup.model_dump(exclude_none=True)
            if not self.premium_ok:
                for row in dump.get("inline_keyboard", []):
                    for btn in row:
                        btn.pop("icon_custom_emoji_id", None)
            data["reply_markup"] = dump
        return Message.model_validate(data, context={"bot": bot})

    def chat(self, chat_id: int) -> list[ChatMessage]:
        return sorted((m for (c, _), m in self.messages.items() if c == chat_id), key=lambda m: m.id)

    def _check_post(self, method: TelegramMethod, chat_id: int) -> None:
        if chat_id in self.gone:
            raise TelegramForbiddenError(method, "Forbidden: bot is not a member of the channel chat")
        if chat_id in self.no_rights:
            raise TelegramBadRequest(method, "Bad Request: not enough rights to send text messages to the chat")

    def _media_source(self, src: Any) -> str:
        if isinstance(src, InputFile):
            self.uploads += 1
            return f"fid_{next(self._fids)}"
        return src

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=self.bot_id, is_bot=True, first_name="Post", username=f"post{self.bot_id}_bot")
        if isinstance(method, GetWebhookInfo):
            from aiogram.types import WebhookInfo
            return WebhookInfo(url="", has_custom_certificate=False, pending_update_count=0)
        if isinstance(method, DeleteWebhook):
            return True
        if isinstance(method, SetMyCommands):
            return True
        if isinstance(method, AnswerCallbackQuery):
            if method.show_alert and method.text:
                self.alerts.append(method.text)
            return True
        if isinstance(method, DeleteMessage):
            key = (method.chat_id, method.message_id)
            if key not in self.messages:
                raise TelegramBadRequest(method, "Bad Request: message to delete not found")
            del self.messages[key]
            return True
        if isinstance(method, SendMessage):
            self._check_post(method, method.chat_id)
            return self._store(bot, method.chat_id, "text", method.reply_markup, text=method.text)
        if isinstance(method, (SendPhoto, SendAnimation, SendVideo, SendDocument, SendAudio)):
            self._check_post(method, method.chat_id)
            kind = {SendPhoto: "photo", SendAnimation: "animation", SendVideo: "video",
                    SendDocument: "document", SendAudio: "audio"}[type(method)]
            src = getattr(method, kind)
            if isinstance(src, InputFile) and kind == "document":
                path = getattr(src, "path", None)
                if path:
                    with open(path, "rb") as f:
                        self.sent_files.append(f.read())
            fid = self._media_source(src)
            extra = {k: getattr(method, k, None) for k in ("has_spoiler", "show_caption_above_media",
                                                          "disable_notification", "protect_content")}
            return self._store(bot, method.chat_id, kind, method.reply_markup, caption=method.caption,
                               file_id=fid, extra=extra)
        if isinstance(method, EditMessageText):
            m = self.messages.get((method.chat_id, method.message_id))
            if m is None:
                raise TelegramBadRequest(method, "Bad Request: message to edit not found")
            if m.kind != "text":
                raise TelegramBadRequest(method, "Bad Request: there is no text in the message to edit")
            if m.text == method.text and _same(m.markup, method.reply_markup):
                raise TelegramBadRequest(method, "Bad Request: message is not modified")
            m.text, m.markup = method.text, method.reply_markup
            return self.msg(bot, m)
        if isinstance(method, EditMessageReplyMarkup):
            m = self.messages.get((method.chat_id, method.message_id))
            if m is None:
                raise TelegramBadRequest(method, "Bad Request: message to edit not found")
            if _same(m.markup, method.reply_markup):
                raise TelegramBadRequest(method, "Bad Request: message is not modified")
            m.markup = method.reply_markup
            return self.msg(bot, m)
        if isinstance(method, EditMessageMedia):
            m = self.messages.get((method.chat_id, method.message_id))
            if m is None or m.kind == "text":
                raise TelegramBadRequest(method, "Bad Request: message can't be edited")
            m.file_id = self._media_source(method.media.media)
            m.kind, m.caption, m.markup = method.media.type, method.media.caption, method.reply_markup
            return self.msg(bot, m)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id, file_path="f/x")
        if isinstance(method, PinChatMessage):
            self.pinned.append((method.chat_id, method.message_id))
            return True
        if isinstance(method, LeaveChat):
            self.left.append(method.chat_id)
            return True
        if isinstance(method, GetChat):
            cid = method.chat_id
            if isinstance(cid, str):
                cid = next((k for k, c in self.chats.items() if "@" + (c.get("username") or "") == cid), 0)
            info = self.chats.get(cid)
            if info is None:
                raise TelegramBadRequest(method, "Bad Request: chat not found")
            gifts = dict(unlimited_gifts=True, limited_gifts=True, unique_gifts=True, premium_subscription=True,
                         gifts_from_channels=True)
            return ChatFullInfo(id=cid, type=info["type"], title=info["title"], username=info.get("username"),
                                accent_color_id=0, max_reaction_count=11, accepted_gift_types=gifts)
        if isinstance(method, GetChatMember):
            bot_user = User(id=self.bot_id, is_bot=True, first_name="Post")
            if method.chat_id in self.gone:
                return ChatMemberLeft(user=bot_user)
            rights = dict(BOT_RIGHTS)
            if method.chat_id in self.no_rights:
                rights["can_post_messages"] = False
            return ChatMemberAdministrator(user=bot_user, **rights)
        raise NotImplementedError(type(method).__name__)

    def _store(self, bot: Bot, chat_id: int, kind: str, markup: Any, text: str | None = None,
               caption: str | None = None, file_id: str | None = None, extra: dict | None = None) -> Message:
        m = ChatMessage(next(self._ids), chat_id, text, caption, kind, markup, file_id, extra or {})
        self.messages[(chat_id, m.id)] = m
        return self.msg(bot, m)


def _same(a: Any, b: Any) -> bool:
    dump = lambda x: x.model_dump(exclude_none=True) if x is not None else None  # noqa: E731
    return dump(a) == dump(b)
