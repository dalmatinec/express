"""Сквозные тесты: бот целиком на фейковом Telegram.
Запуск: python -m unittest -v"""
import asyncio
import io
import itertools
import tempfile
import unittest
import zipfile
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update

from postbot import handlers, ui
from postbot.__main__ import setup
from postbot.config import Config
from postbot.services import posts as P
from postbot.services.backup import make_backup
from postbot.services.publisher import run_due

from .fake_telegram import BOT_RIGHTS, ChatMessage, FakeTelegram

OWNER, ADMIN, STRANGER = 1, 2, 3
CHAN, CHAN2 = -1001111, -1002222
EMOJI = "5390000000000000001"
_ids = itertools.count(1)


class Harness:
    def __init__(self, tmp: Path, bot_id: int = 1000) -> None:
        self.tmp = tmp
        self.tg = FakeTelegram(bot_id=bot_id)
        self.tg.chats[CHAN] = {"title": "Канал", "type": "channel", "username": "chan"}
        self.tg.chats[CHAN2] = {"title": "Второй", "type": "channel"}
        self.bot = Bot(f"{bot_id}:TEST", session=self.tg,
                       default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
        self.config = Config(bot_token="x", owner_ids=frozenset({OWNER}), data_dir=tmp)

    async def start(self) -> "Harness":
        for module in (handlers.chats, handlers.common, handlers.editor, handlers.buttons, handlers.publish,
                       handlers.history, handlers.admin, handlers.fallback):
            module.router._parent_router = None
        self.app, self.dp = await setup(self.config, self.bot)
        return self

    async def stop(self) -> None:
        await self.app.db.close()

    async def feed(self, **update) -> None:
        await self.dp.feed_update(self.bot, Update.model_validate({"update_id": next(_ids), **update},
                                                                   context={"bot": self.bot}))

    @staticmethod
    def user(uid: int) -> dict:
        return {"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"user{uid}"}

    async def send(self, uid: int, text: str | None = None, **extra) -> int:
        mid = next(self.tg._ids)
        self.tg.messages[(uid, mid)] = ChatMessage(mid, uid, text, None, "text", None)
        msg = {"message_id": mid, "date": 0, "chat": {"id": uid, "type": "private"}, "from": self.user(uid), **extra}
        if text is not None:
            msg["text"] = text
            if text.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self.feed(message=msg)
        return mid

    def panel(self, uid: int = OWNER) -> ChatMessage:
        return self.tg.messages[(uid, self.app.screens[uid]["panel"])]

    def preview(self, uid: int = OWNER) -> ChatMessage:
        return self.tg.messages[(uid, self.app.screens[uid]["preview"])]

    def buttons(self, m: ChatMessage) -> list[list]:
        return m.markup.inline_keyboard if m.markup is not None else []

    def find(self, text_part: str, uid: int = OWNER) -> str:
        """callback_data кнопки текущей панели по куску текста."""
        for row in self.buttons(self.panel(uid)):
            for btn in row:
                if text_part in btn.text and btn.callback_data:
                    return btn.callback_data
        raise AssertionError(f"нет кнопки {text_part!r} в {[[b.text for b in r] for r in self.buttons(self.panel(uid))]}")

    async def click(self, data: str, uid: int = OWNER, message_id: int | None = None) -> None:
        mid = message_id or self.app.screens[uid]["panel"]
        m = self.tg.messages[(uid if mid in [x.id for x in self.tg.chat(uid)] else uid, mid)]
        await self.feed(callback_query={
            "id": str(next(_ids)), "from": self.user(uid), "chat_instance": "c", "data": data,
            "message": self.tg.msg(self.bot, m).model_dump(by_alias=True, exclude_none=True),
        })

    async def press(self, text_part: str, uid: int = OWNER) -> None:
        await self.click(self.find(text_part, uid), uid)

    async def add_channel(self, chat_id: int = CHAN, by: int = OWNER) -> None:
        bot_user = {"id": self.tg.bot_id, "is_bot": True, "first_name": "Post"}
        info = self.tg.chats[chat_id]
        await self.feed(my_chat_member={
            "chat": {"id": chat_id, "type": info["type"], "title": info["title"], "username": info.get("username")},
            "from": self.user(by), "date": 0,
            "old_chat_member": {"status": "left", "user": bot_user},
            "new_chat_member": {"status": "administrator", "user": bot_user, **BOT_RIGHTS}})

    async def new_post(self, text: str = "Привет, мир", **extra) -> int:
        await self.send(OWNER, ui.MAIN_CREATE)
        await self.send(OWNER, text, **extra)
        return self.app.screens[OWNER]["preview_post"]


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.h = await Harness(Path(self._tmp.name)).start()

    async def asyncTearDown(self) -> None:
        await self.h.stop()
        self._tmp.cleanup()


class AccessTests(Base):
    async def test_owner_gets_menu_stranger_does_not(self) -> None:
        await self.h.send(OWNER, "/start")
        self.assertIn("конструктор постов", self.h.panel().text)
        await self.h.send(STRANGER, "/start")
        await self.h.send(STRANGER, "/start")
        replies = [m for m in self.h.tg.chat(STRANGER) if m.text and "только для администраторов" in m.text]
        self.assertEqual(len(replies), 1)

    async def test_channel_connects_itself(self) -> None:
        await self.h.add_channel()
        c = await self.h.app.channel(CHAN)
        self.assertTrue(c.ok)
        self.assertTrue(any("подключён" in (m.text or "") for m in self.h.tg.chat(OWNER)))

    async def test_stranger_cannot_connect_channel(self) -> None:
        await self.h.add_channel(by=STRANGER)
        self.assertIsNone(await self.h.app.channel(CHAN))


class EditorTests(Base):
    async def test_create_with_premium_emoji_and_buttons(self) -> None:
        h = self.h
        pid = await h.new_post("⭐ Пост", entities=[{"type": "custom_emoji", "offset": 0, "length": 1,
                                                    "custom_emoji_id": EMOJI}])
        self.assertIn(f'emoji-id="{EMOJI}"', h.preview().text)
        self.assertIn("Новый пост", h.panel().text)

        # кнопки списком: 2 в первом ряду, 1 во втором
        await h.press("Кнопки")
        await h.press("Списком")
        await h.send(OWNER, "Канал - https://t.me/chan | Чат @chat\nСайт site.com")
        rows = h.buttons(h.preview())
        self.assertEqual([[b.text for b in r] for r in rows], [["Канал", "Чат"], ["Сайт"]])
        self.assertEqual(rows[1][0].url, "https://site.com")

        # двигаем Сайт: вверх - встаёт в ряд выше
        btns = await P.get_buttons(h.app, pid)
        site = next(b for b in btns if b.label == "Сайт")
        await h.click(f"k:{pid}:mv:{site.id}")
        await h.click(f"k:{pid}:md:{site.id}:up")
        self.assertEqual([[b.text for b in r] for r in h.buttons(h.preview())], [["Канал", "Чат", "Сайт"]])
        # ещё раз вверх из ряда из трёх: встаёт отдельным рядом выше
        await h.click(f"k:{pid}:md:{site.id}:up")
        self.assertEqual([[b.text for b in r] for r in h.buttons(h.preview())], [["Сайт"], ["Канал", "Чат"]])
        await h.click(f"k:{pid}:md:{site.id}:left")  # некуда
        self.assertEqual([[b.text for b in r] for r in h.buttons(h.preview())], [["Сайт"], ["Канал", "Чат"]])

        # цвет
        await h.click(f"k:{pid}:col:{site.id}:success")
        self.assertEqual(h.buttons(h.preview())[0][0].style, "success")

        # новая кнопка-всплывашка с премиум-иконкой
        await h.click(f"k:{pid}:add")
        await h.send(OWNER, "⭐ Секрет", entities=[{"type": "custom_emoji", "offset": 0, "length": 1,
                                                   "custom_emoji_id": EMOJI}])
        await h.click(f"k:{pid}:addk:popup")
        await h.send(OWNER, "Промокод: LETO")
        last = h.buttons(h.preview())[-1][0]
        self.assertEqual(last.text, "Секрет")
        self.assertEqual(last.icon_custom_emoji_id, EMOJI)
        await h.click(last.callback_data, message_id=h.app.screens[OWNER]["preview"])
        self.assertEqual(h.tg.alerts[-1], "Промокод: LETO")

        # в чате только предпросмотр и панель, всё остальное убрано
        self.assertEqual(len(h.tg.chat(OWNER)), 2)

    async def test_text_limit_with_media(self) -> None:
        h = self.h
        await h.send(OWNER, ui.MAIN_CREATE)
        photo = [{"file_id": "ph1", "file_unique_id": "ph1", "width": 1, "height": 1, "file_size": 10}]
        await h.send(OWNER, None, photo=photo, caption="x" * 1100)
        self.assertIn("Слишком длинный", h.panel().text)
        await h.send(OWNER, None, photo=photo, caption="Фото")
        pid = h.app.screens[OWNER]["preview_post"]
        post = await P.get_post(h.app, pid)
        media = await h.app.media.get(post.media_id)
        self.assertEqual(media.kind, "photo")
        self.assertTrue(media.path.exists())
        self.assertEqual(h.preview().kind, "photo")

    async def test_cancel_drops_draft(self) -> None:
        h = self.h
        pid = await h.new_post()
        await h.press("Отмена")
        await h.press("Да, выйти")
        self.assertIsNone(await P.get_post(h.app, pid))


class PublishTests(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.h.add_channel(CHAN)
        await self.h.add_channel(CHAN2)

    async def _publish_new(self) -> int:
        h = self.h
        pid = await h.new_post("Первый пост")
        await h.press("Кнопки")
        await h.press("Списком")
        await h.send(OWNER, "Канал @chan")
        await h.click(f"e:{pid}:set")
        await h.click(f"e:{pid}:tg:pin")
        await h.click(f"e:{pid}:pub")
        self.assertIn("название", h.panel().text)
        await h.send(OWNER, "Мой первый")
        await h.press("Канал")
        await h.press("Опубликовать сейчас")
        return pid

    async def test_publish_now_history_edit_delete(self) -> None:
        h = self.h
        pid = await self._publish_new()
        posted = [m for m in h.tg.chat(CHAN)]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0].text, "Первый пост")
        self.assertEqual(posted[0].markup.inline_keyboard[0][0].url, "https://t.me/chan")
        self.assertIn((CHAN, posted[0].id), h.tg.pinned)
        self.assertEqual(h.tg.chat(CHAN2), [])
        self.assertIn("Опубликовано: 1 из 1", h.panel().text)
        self.assertIn("t.me/chan/", h.panel().text)

        post = await P.get_post(h.app, pid)
        self.assertEqual((post.is_draft, post.title), (0, "Мой первый"))

        # правка и обновление в канале
        await h.press("Изменить и опубликовать")
        draft_id = h.app.screens[OWNER]["preview_post"]
        self.assertNotEqual(draft_id, pid)
        await h.click(f"e:{draft_id}:text")
        await h.send(OWNER, "Исправленный пост")
        await h.press("Сохранить и обновить")
        self.assertEqual(h.tg.chat(CHAN)[0].text, "Исправленный пост")
        self.assertIsNone(await P.get_post(h.app, draft_id))
        self.assertEqual((await P.get_post(h.app, pid)).html, "Исправленный пост")
        self.assertIn("Обновлено в каналах: 1 из 1", h.panel().text)

        # история
        await h.send(OWNER, ui.MAIN_HISTORY)
        self.assertIn("История постов (1)", h.panel().text)
        await h.press("Мой первый")
        await h.press("Переименовать")
        await h.send(OWNER, "Новое имя")
        self.assertIn("Новое имя", h.panel().text)

        # удалить из каналов
        await h.press("Удалить из каналов")
        await h.press("Да, удалить")
        self.assertEqual(h.tg.chat(CHAN), [])

        await h.press("Удалить из истории")
        await h.press("Да, удалить")
        self.assertIsNone(await P.get_post(h.app, pid))

    async def test_no_rights_channel_is_reported(self) -> None:
        h = self.h
        h.tg.no_rights.add(CHAN2)
        pid = await h.new_post("Пост")
        await h.click(f"e:{pid}:pub")
        await h.click(f"e:{pid}:notitle:pub")
        await h.press("Выбрать все")
        await h.press("Опубликовать сейчас")
        text = h.panel().text
        self.assertIn("Опубликовано: 1 из 2", text)
        self.assertIn("Второй: у бота нет прав", text)
        # после ошибки бот перепроверил права, теперь канал помечен
        self.assertFalse((await h.app.channel(CHAN2)).ok)

    async def test_scheduled_and_autodelete(self) -> None:
        h = self.h
        pid = await h.new_post("Отложенный")
        await h.click(f"e:{pid}:set")
        await h.click(f"e:{pid}:tg:autodel")  # 1 час
        await h.click(f"e:{pid}:pub")
        await h.click(f"e:{pid}:notitle:pub")
        await h.press("Канал")
        await h.press("По времени")
        await h.send(OWNER, "+5м")
        self.assertIn("Запланировано", h.panel().text)
        self.assertEqual(h.tg.chat(CHAN), [])

        await h.app.db.execute("UPDATE publications SET run_at = 0")
        await run_due(h.app)
        self.assertEqual(len(h.tg.chat(CHAN)), 1)
        pub = (await P.publications(h.app, pid))[0]
        self.assertEqual(pub.status, "sent")
        self.assertTrue(pub.delete_at)
        self.assertTrue(any("Отложенный пост" in (m.text or "") for m in h.tg.chat(OWNER)))

        await h.app.db.execute("UPDATE publications SET delete_at = 1")
        await run_due(h.app)
        self.assertEqual(h.tg.chat(CHAN), [])
        self.assertEqual((await P.publications(h.app, pid))[0].status, "deleted")


class AdminTests(Base):
    async def test_admin_with_limited_channels(self) -> None:
        h = self.h
        await h.add_channel(CHAN)
        await h.add_channel(CHAN2)
        await h.send(OWNER, ui.MAIN_ADMIN)
        await h.press("Админы")
        await h.press("Добавить админа")
        await h.send(OWNER, None, users_shared={"request_id": 3, "users": [{"user_id": ADMIN, "first_name": "Вася"}]})
        self.assertIn(ADMIN, h.app.admins)
        await h.click(f"a:admall:{ADMIN}")
        await h.click(f"a:admt:{ADMIN}:{CHAN2}")
        allowed = await h.app.channels_for(ADMIN)
        self.assertEqual([c.id for c in allowed], [CHAN2])

        await h.send(ADMIN, "/start")
        self.assertIn("конструктор", h.panel(ADMIN).text)
        await h.send(ADMIN, ui.MAIN_ADMIN)
        texts = [b.text for r in h.buttons(h.panel(ADMIN)) for b in r]
        self.assertFalse(any("Бэкап" in t for t in texts))

    async def test_connect_by_username(self) -> None:
        h = self.h
        await h.send(OWNER, ui.MAIN_ADMIN)
        await h.press("Каналы")
        await h.press("Подключить")
        await h.send(OWNER, "@chan")
        self.assertTrue((await h.app.channel(CHAN)).ok)


class BackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_backup_and_move_to_new_token(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            old = await Harness(Path(a), bot_id=1000).start()
            self.addAsyncCleanup(old.stop)
            await old.add_channel(CHAN)
            await old.send(OWNER, ui.MAIN_CREATE)
            photo = [{"file_id": "ph1", "file_unique_id": "ph1", "width": 1, "height": 1, "file_size": 10}]
            await old.send(OWNER, None, photo=photo, caption="С фото")
            pid = old.app.screens[OWNER]["preview_post"]
            await old.click(f"e:{pid}:save")
            await old.click(f"e:{pid}:notitle:save")
            await old.send(OWNER, ui.MAIN_ADMIN)
            await old.press("Бэкап")
            await old.press("Сделать бэкап сейчас")
            self.assertTrue(old.app.config.backup_path.exists())
            self.assertEqual(len(old.tg.sent_files), 1)
            data = old.tg.sent_files[0]
            await make_backup(old.app)  # второй раз: файл на сервере всё равно один
            self.assertEqual(len(list(Path(a).glob("*.zip"))), 1)
            await old.stop()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                self.assertIn("bot.db", zf.namelist())
                self.assertTrue(any(n.startswith("media/") for n in zf.namelist()))

            new = await Harness(Path(b), bot_id=2000).start()
            self.addAsyncCleanup(new.stop)
            new.tg.download_data = data
            await new.send(OWNER, ui.MAIN_ADMIN)
            await new.press("Бэкап")
            await new.press("Восстановить")
            await new.send(OWNER, None, document={"file_id": "bk", "file_unique_id": "bk", "file_name": "b.zip",
                                                  "file_size": len(data)})
            self.assertIn("Восстановлено. Постов в истории: 1", new.panel().text)

            # публикуем старый пост новым ботом: медиа заливается с диска
            await new.send(OWNER, ui.MAIN_HISTORY)
            await new.click(f"h:{pid}:re")
            await new.press("Опубликовать сейчас")  # канал один, он уже отмечен
            self.assertEqual(new.tg.chat(CHAN)[0].kind, "photo")
            self.assertGreaterEqual(new.tg.uploads, 1)
            self.assertTrue(await new.app.media.file_id((await P.get_post(new.app, pid)).media_id))
            await new.stop()


if __name__ == "__main__":
    unittest.main()
