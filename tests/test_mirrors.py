"""Зеркала, запасные токены, автопереключение основного бота и уведомление зеркалам."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update

from bot.app import use_bot
from bot.config import Config
from bot.runtime import MainRunner, pick_main_token

from .fake_telegram import ChatMessage, FakeTelegram
from .test_flow import OTHER, OWNER, USER, Harness, _ids


def TOKEN(bot_id: int) -> str:
    return f"{bot_id}:" + "A" * 35


class FakeBots:
    """Фабрика ботов для тестов: у каждого токена свой фейковый Telegram."""

    def __init__(self) -> None:
        self.tgs: dict[int, FakeTelegram] = {}

    def tg(self, bot_id: int) -> FakeTelegram:
        return self.tgs.setdefault(bot_id, FakeTelegram(bot_id=bot_id))

    def __call__(self, token: str) -> Bot:
        bot_id = int(token.split(":")[0])
        return Bot(token, session=self.tg(bot_id),
                   default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))

    async def close(self) -> None:
        pass


class MirrorHarness(Harness):
    def __init__(self, tmp: Path) -> None:
        super().__init__(tmp)
        self.config = Config(bot_token="x", owner_ids=frozenset({OWNER}), data_dir=tmp, log_level="INFO",
                             secret_key="test-secret")
        self.bots = FakeBots()
        self.bots.tgs[1000] = self.tg

    async def start(self) -> "MirrorHarness":
        await super().start()
        self.app.mirrors.factory = self.bots
        self.app.reserves.factory = self.bots
        self.runner: MainRunner | None = None
        return self

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.stop()
        await self.app.mirrors.stop_all()
        await super().stop()

    # ---- обновления для зеркала ----
    def mirror_bot(self, bot_id: int) -> Bot:
        return next(b for b in self.app.mirrors.running_bots() if b.id == bot_id)

    async def mirror_feed(self, bot_id: int, **update) -> None:
        bot = self.mirror_bot(bot_id)
        with use_bot(bot):
            await self.app.mirrors.dp.feed_update(
                bot, Update.model_validate({"update_id": next(_ids), **update}, context={"bot": bot}))

    async def mirror_send(self, bot_id: int, uid: int, text: str) -> None:
        tg = self.bots.tg(bot_id)
        mid = next(tg._ids)
        tg.messages[(uid, mid)] = ChatMessage(mid, uid, text, None, "text", None)
        msg = {"message_id": mid, "date": 0, "chat": {"id": uid, "type": "private"}, "from": self.user(uid),
               "text": text}
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self.mirror_feed(bot_id, message=msg)

    async def mirror_click(self, bot_id: int, uid: int, data: str, message_id: int) -> None:
        tg = self.bots.tg(bot_id)
        m = tg.messages[(uid, message_id)]
        await self.mirror_feed(bot_id, callback_query={
            "id": str(next(_ids)), "from": self.user(uid), "chat_instance": "c", "data": data,
            "message": tg.msg(self.mirror_bot(bot_id), m).model_dump(by_alias=True, exclude_none=True)})

    def mirror_screen(self, bot_id: int, uid: int) -> ChatMessage:
        with use_bot(self.mirror_bot(bot_id)):
            return self.bots.tg(bot_id).messages[(uid, self.app.screens[uid].message_id)]

    async def main_click(self, uid: int, data: str, message_id: int, chat: int | None = None) -> None:
        """Нажатие в основном боте уже после переключения (старый объект бота мёртв).
        chat - чей это чат с сообщением, если кнопку нажал не его хозяин."""
        bot = self.app.main_bot
        tg = self.bots.tg(bot.id)
        m = tg.messages[(chat or uid, message_id)]
        await self.dp.feed_update(bot, Update.model_validate({"update_id": next(_ids), "callback_query": {
            "id": str(next(_ids)), "from": self.user(uid), "chat_instance": "c", "data": data,
            "message": tg.msg(bot, m).model_dump(by_alias=True, exclude_none=True)}}, context={"bot": bot}))

    def start_runner(self) -> MainRunner:
        self.runner = MainRunner(self.app, self.dp, self.bots, polling_timeout=0)
        self.app.runner = self.runner
        self.runner.start()
        return self.runner

    async def wait_for(self, cond, timeout: float = 5.0) -> None:
        for _ in range(int(timeout / 0.02)):
            if cond():
                return
            await asyncio.sleep(0.02)
        raise AssertionError("не дождались условия")


class MirrorBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.h = await MirrorHarness(Path(self._tmp.name)).start()
        self.app, self.tg, self.bots = self.h.app, self.h.tg, self.h.bots

    async def asyncTearDown(self) -> None:
        await self.h.stop()
        self._tmp.cleanup()


class Mirrors(MirrorBase):
    async def test_person_adds_mirror_and_it_serves_links(self):
        h, app = self.h, self.app
        item_id = await h.make_item()
        await app.links.refill()
        await h.send(USER, "/start")
        buttons = [b.callback_data for row in h.user_screen(USER).markup.inline_keyboard for b in row]
        self.assertIn("mir", buttons, "в меню есть кнопка создания зеркала")

        await h.click(USER, "mir")
        self.assertIn("BotFather", h.user_screen(USER).text)
        text = h.user_screen(USER).text
        import re as _re
        self.assertRegex(text, r"имя: <code>U\d+ LINK</code>|имя: <code>.* LINK</code>")
        uname = _re.search(r"юзернейм: <code>(\w+)</code>", text)[1]
        self.assertRegex(uname, r"^\w+_link_[a-z0-9]{4}_bot$")
        self.assertGreaterEqual(len(uname), 5)
        await h.click(USER, "mirn")
        self.assertNotEqual(_re.search(r"юзернейм: <code>(\w+)</code>", h.user_screen(USER).text)[1], uname,
                            "кнопка даёт другой вариант")
        mid = await h.send(USER, TOKEN(2001))
        self.assertNotIn((USER, mid), self.tg.messages, "сообщение с токеном удалено из чата")
        self.assertIn("@links2001_bot", h.user_screen(USER).text)

        row = await app.db.fetchone("SELECT token_enc, owner_id, status FROM mirrors WHERE bot_id = 2001")
        self.assertEqual((row["owner_id"], row["status"]), (USER, "active"))
        self.assertNotIn(b"AAAAAAAA", bytes(row["token_enc"]), "токен в базе зашифрован")
        self.assertTrue(app.mirrors.is_running(2001))

        # другой человек пользуется зеркалом
        mtg = self.bots.tg(2001)
        await h.mirror_send(2001, OTHER, "/start")
        home = h.mirror_screen(2001, OTHER)
        self.assertIn("Привет, U43", home.text)
        self.assertEqual(self.tg.chat(OTHER), [], "в основном боте этому человеку ничего не писали")
        await h.mirror_click(2001, OTHER, f"i:{item_id}", home.id)
        link_msgs = [m for m in mtg.chat(OTHER) if m.markup and (m.markup.inline_keyboard[0][0].url or "").startswith("https://t.me/+")]
        self.assertEqual(len(link_msgs), 1, "ссылка пришла в зеркало")
        self.assertEqual(mtg.links_created, 0, "зеркало ссылок не создаёт")
        owner = await app.db.fetchval("SELECT user_id FROM invite_links WHERE user_id = ?", (OTHER,))
        self.assertEqual(owner, OTHER, "ссылка выдана из общего запаса основного бота")

        # закрытие сообщения в зеркале
        await h.mirror_click(2001, OTHER, f"cl:{item_id}", link_msgs[0].id)
        self.assertNotIn((OTHER, link_msgs[0].id), mtg.messages)

        pairs = {(r["user_id"], r["bot_id"]) for r in await app.db.fetchall("SELECT user_id, bot_id FROM user_bots")}
        self.assertIn((USER, 1000), pairs)
        self.assertIn((OTHER, 2001), pairs)

    async def test_screens_are_separate_between_main_and_mirror(self):
        h, app = self.h, self.app
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await h.send(USER, "/start")
        main_screen = h.user_screen(USER)
        await h.mirror_send(2001, USER, "/start")  # у зеркала нумерация сообщений своя и может совпасть
        mirror_screen = h.mirror_screen(2001, USER)
        self.assertEqual(main_screen.id, mirror_screen.id, "для проверки номера нарочно совпадают")
        await h.send(USER, "/start")  # новый экран в основном боте удаляет только старый основной
        self.assertNotIn((USER, main_screen.id), self.tg.messages)
        self.assertIn((USER, mirror_screen.id), self.bots.tg(2001).messages, "экран зеркала не тронут")

    async def test_bad_tokens_and_limits(self):
        app = self.app
        from bot.mirrors import MirrorError
        with self.assertRaises(MirrorError):
            await app.mirrors.add("not a token", USER)
        with self.assertRaises(MirrorError) as c:
            await app.mirrors.add(TOKEN(1000), USER)
        self.assertIn("основному", str(c.exception))
        self.bots.tg(2002).unauthorized = True
        with self.assertRaises(MirrorError) as c:
            await app.mirrors.add(TOKEN(2002), USER)
        self.assertIn("не принял", str(c.exception))

        await app.mirrors.add(TOKEN(2001), USER)
        with self.assertRaises(MirrorError) as c:
            await app.mirrors.add(TOKEN(2001), OTHER)
        self.assertIn("уже подключён", str(c.exception))
        with self.assertRaises(MirrorError) as c:
            await app.mirrors.add(TOKEN(2003), USER)
        self.assertIn("одно на человека", str(c.exception))
        await app.mirrors.add(TOKEN(2005), OTHER)  # у другого человека свой лимит
        # зеркало владельца бота лимитом не ограничено
        await app.mirrors.add(TOKEN(2006), OWNER)
        await app.mirrors.add(TOKEN(2007), OWNER)

    async def test_my_mirror_screen_replace_and_delete(self):
        h, app = self.h, self.app
        await h.send(USER, "/start")
        await h.click(USER, "mir")
        await h.send(USER, TOKEN(2001))
        await h.send(USER, "/start")
        labels = [b.text for row in h.user_screen(USER).markup.inline_keyboard for b in row]
        self.assertIn("🪞 Моё зеркало", labels)
        await h.click(USER, "mir")
        self.assertIn("@links2001_bot", h.user_screen(USER).text)
        self.assertIn("работает", h.user_screen(USER).text)
        urls = [b.url for row in h.user_screen(USER).markup.inline_keyboard for b in row if b.url]
        self.assertEqual(urls, ["https://t.me/links2001_bot"], "есть кнопка перехода в зеркало")

        await h.click(USER, "mirr")
        await h.send(USER, TOKEN(2002))
        self.assertTrue(app.mirrors.is_running(2002))
        self.assertFalse(app.mirrors.is_running(2001), "прежнее зеркало отключено после замены")
        self.assertEqual([m["bot_id"] for m in await app.mirrors.of_owner(USER)], [2002])

        await h.click(USER, "mird")
        await h.click(USER, "mirdy")
        self.assertEqual(await app.mirrors.of_owner(USER), [])
        self.assertFalse(app.mirrors.is_running(2002))
        await h.send(USER, "/start")
        labels = [b.text for row in h.user_screen(USER).markup.inline_keyboard for b in row]
        self.assertIn("🪞 Создать зеркало", labels)

    async def test_daily_summary_goes_to_owner_once(self):
        from bot import jobs
        app = self.app
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await app.set_setting("summary_hour", 0)
        await jobs.daily_summary(app)
        await jobs.daily_summary(app)
        texts = [m.text for m in self.tg.chat(OWNER) if m.text and "Сводка за сутки" in m.text]
        self.assertEqual(len(texts), 1, "за день одна сводка")
        self.assertIn("Зеркал работает: <b>1</b> из 1", texts[0])
        self.assertIn("Запасных токенов готово", texts[0])

    async def test_mirror_off_without_secret_key_and_hidden_button(self):
        self.app.cipher = None
        await self.h.send(USER, "/start")
        buttons = [b.callback_data for row in (self.h.user_screen(USER).markup.inline_keyboard
                   if self.h.user_screen(USER).markup else []) for b in row]
        self.assertNotIn("mir", buttons)

    async def test_revoked_mirror_is_switched_off_and_owner_told(self):
        h, app = self.h, self.app
        await h.send(USER, "/start")
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        self.bots.tg(2001).unauthorized = True
        await h.wait_for(lambda: not app.mirrors.is_running(2001))
        row = await app.db.fetchone("SELECT status FROM mirrors WHERE bot_id = 2001")
        self.assertEqual(row["status"], "revoked")
        self.assertIn("перестало работать", self.tg.last(USER).text)

    async def test_mirrors_restart_from_database(self):
        h, app = self.h, self.app
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await app.mirrors.stop_all()
        self.assertFalse(app.mirrors.is_running(2001))
        await app.mirrors.start_all()
        self.assertTrue(app.mirrors.is_running(2001))


class Failover(MirrorBase):
    async def test_main_dies_reserve_takes_over_and_mirrors_are_notified(self):
        h, app = self.h, self.app
        await app.set_setting("auto_notify", 0)  # здесь проверяем вариант «спросить владельца»
        await h.make_item()
        await app.links.refill()
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await h.mirror_send(2001, OTHER, "/start")
        reserve, problems = await app.reserves.add(TOKEN(3001))
        self.assertEqual((reserve.username, problems), ("links3001_bot", []))
        await h.send(USER, "/start")
        self.assertIn(USER, app.screens)
        h.start_runner()

        self.tg.unauthorized = True  # основной бот заблокирован
        await h.wait_for(lambda: app.main_bot.id == 3001)
        await h.wait_for(lambda: not h.runner.state.down and USER not in app.screens)
        self.assertEqual(len(app.link_msgs), 0, "номера сообщений старого бота забыты")
        self.assertEqual(app.bot_username, "links3001_bot")
        row = await app.db.fetchone("SELECT status FROM reserve_tokens WHERE bot_id = 3001")
        self.assertEqual(row["status"], "active")
        self.assertEqual((app.store.setting("main_override"))["bot_id"], 3001)
        free = await app.db.fetchval("SELECT COUNT(*) FROM invite_links WHERE user_id IS NULL AND revoked = 0")
        self.assertEqual(free, 0, "готовые ссылки старого бота убраны из запаса")
        self.assertTrue(app.links.wake.is_set())

        # владелец получил вопрос с кнопками
        new_tg = self.bots.tg(3001)
        await h.wait_for(lambda: bool(new_tg.chat(OWNER)))
        ask = new_tg.last(OWNER)
        self.assertIn("links3001_bot", ask.text)
        data = [b.callback_data for row in ask.markup.inline_keyboard for b in row]
        self.assertEqual(data, ["mn:y:3001", "mn:n"])

        await h.main_click(OWNER, "mn:y:3001", ask.id)
        await app.notifier.wait()
        st = app.notifier.state
        self.assertEqual((st.sent, st.blocked, st.failed), (2, 0, 0))  # зеркалу - OTHER, новому основному - владельцу
        last = self.bots.tg(2001).last(OTHER)
        self.assertIn("@links3001_bot", last.text)
        self.assertIn("Основной бот сменил адрес", last.text)

        # после перезапуска берётся выбранный запасной, а не токен из .env
        token = await pick_main_token(h.config, self.bots)
        self.assertEqual(token, TOKEN(3001))

    async def test_ban_of_main_notifies_mirrors_automatically(self):
        h, app = self.h, self.app
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await h.mirror_send(2001, OTHER, "/start")
        await app.reserves.add(TOKEN(3001))
        h.start_runner()
        self.tg.unauthorized = True
        await h.wait_for(lambda: app.main_bot.id == 3001)
        await h.wait_for(lambda: app.notifier.state is not None)
        await app.notifier.wait()
        last = self.bots.tg(2001).last(OTHER)
        self.assertIn("@links3001_bot", last.text, "зеркало получило новый адрес без вопроса")
        new_tg = self.bots.tg(3001)
        await h.wait_for(lambda: any("автоматически" in (m.text or "") for m in new_tg.chat(OWNER)))
        notes = [m for m in new_tg.chat(OWNER) if "автоматически" in (m.text or "")]
        self.assertIsNone(notes[-1].markup, "кнопок Да/Нет нет")

    async def test_dead_reserve_is_skipped_and_marked(self):
        h, app = self.h, self.app
        await app.reserves.add(TOKEN(3001))
        await app.reserves.add(TOKEN(3002))
        self.bots.tg(3001).unauthorized = True
        h.start_runner()
        self.tg.unauthorized = True
        await h.wait_for(lambda: app.main_bot.id == 3002)
        rows = {r["bot_id"]: r["status"] for r in await app.db.fetchall("SELECT bot_id, status FROM reserve_tokens")}
        self.assertEqual(rows, {3001: "dead", 3002: "active"})

    async def test_no_reserve_then_one_added_recovers(self):
        h, app = self.h, self.app
        with mock.patch("bot.runtime.RETRY_NO_RESERVE", 0.05):
            h.start_runner()
            self.tg.unauthorized = True
            await h.wait_for(lambda: h.runner.state.down)
            self.assertEqual(app.main_bot.id, 1000)
            await app.reserves.add(TOKEN(3001))
            await h.wait_for(lambda: app.main_bot.id == 3001 and not h.runner.state.down)

    async def test_manual_switch_and_history(self):
        h, app = self.h, self.app
        await app.reserves.add(TOKEN(3001))
        h.start_runner()
        info = await h.runner.failover("вручную из панели", manual=True)
        self.assertTrue(info.manual)
        self.assertEqual(app.main_bot.id, 3001)
        self.assertEqual(h.runner.state.last.new_username, "links3001_bot")
        self.assertTrue(h.runner.running)

    async def test_notify_only_owner_may_confirm_and_no_always_sends(self):
        h, app = self.h, self.app
        await app.reserves.add(TOKEN(3001))
        h.start_runner()
        await h.runner.failover("тест", manual=True)
        new_tg = self.bots.tg(3001)
        await h.wait_for(lambda: bool(new_tg.chat(OWNER)))
        ask = new_tg.last(OWNER)
        await h.main_click(OTHER, "mn:y:3001", ask.id, chat=OWNER)  # посторонний
        self.assertIsNone(app.notifier.state)
        await h.main_click(OWNER, "mn:n", ask.id)
        self.assertIsNone(app.notifier.state)
        self.assertIn("ничего не отправляю", new_tg.messages[(OWNER, ask.id)].text)


class Reserves(MirrorBase):
    async def test_add_validation_and_rights_warnings(self):
        app = self.app
        from bot.mirrors import MirrorError
        await self.h.make_item()  # у основного есть подключённый канал
        self.bots.tg(3001).dead_chats.add(-1001)  # запасной бот в канале без прав
        reserve, problems = await app.reserves.add(TOKEN(3001))
        self.assertEqual(len(problems), 1)
        self.assertIn("бота там нет", problems[0])

        with self.assertRaises(MirrorError):
            await app.reserves.add(TOKEN(3001))      # дубль
        with self.assertRaises(MirrorError):
            await app.reserves.add(TOKEN(1000))      # токен основного
        await app.mirrors.add(TOKEN(2001), USER)
        with self.assertRaises(MirrorError):
            await app.reserves.add(TOKEN(2001))      # уже зеркало
        self.bots.tg(3002).unauthorized = True
        with self.assertRaises(MirrorError):
            await app.reserves.add(TOKEN(3002))      # Telegram не принял
        for n in range(3003, 3007):
            await app.reserves.add(TOKEN(n))
        with self.assertRaises(MirrorError) as c:
            await app.reserves.add(TOKEN(3999))
        self.assertIn("максимум", str(c.exception))

    async def test_remove_but_not_active(self):
        app = self.app
        await app.reserves.add(TOKEN(3001))
        self.assertTrue(await app.reserves.remove(3001))
        await app.reserves.add(TOKEN(3002))
        await app.reserves.activate(3002)
        self.assertFalse(await app.reserves.remove(3002), "работающий основной удалить нельзя")


if __name__ == "__main__":
    unittest.main()


class Disaster(MirrorBase):
    async def test_backup_has_manifest_and_wrong_key_is_refused(self):
        import tempfile, zipfile
        from pathlib import Path
        from bot.backup import make_backup, restore_files
        app = self.app
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        path = await make_backup(app)
        self.assertIn("manifest.json", zipfile.ZipFile(path).namelist())
        with tempfile.TemporaryDirectory() as tmp:
            db, media = Path(tmp) / "bot.db", Path(tmp) / "media"
            with self.assertRaises(ValueError) as c:
                restore_files(path, db, media, "другой-ключ")
            self.assertIn("SECRET_KEY не тот", str(c.exception))
            self.assertFalse(db.exists(), "с чужим ключом ничего не тронуто")
            restore_files(path, db, media, app.config.secret_key)
            self.assertTrue(db.exists())
            restore_files(path, db, media, "другой-ключ", force=True)

    async def test_old_backup_without_manifest_still_restores(self):
        import tempfile, zipfile
        from pathlib import Path
        from bot.backup import make_backup, restore_files
        path = await make_backup(self.app)
        old = path.with_name("old.zip")
        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(old, "w") as zout:
            for n in zin.namelist():
                if n != "manifest.json":
                    zout.writestr(n, zin.read(n))
        with tempfile.TemporaryDirectory() as tmp:
            restore_files(old, Path(tmp) / "bot.db", Path(tmp) / "m", "любой")

    async def test_mirror_change_schedules_light_backup(self):
        from bot import jobs
        app = self.app
        self.assertEqual(app.backup_due, 0)
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        self.assertGreater(app.backup_due, 0)
        app.backup_due = 1  # срок наступил
        sent = []
        orig = jobs.send_backup
        async def fake(app_, caption, with_media=True):
            sent.append((caption, with_media))
            return ""
        jobs.send_backup = fake
        try:
            from bot.store import now
            if app.backup_due and now() >= app.backup_due:
                app.backup_due = 0
                await jobs.send_backup(app, "x", with_media=False)
        finally:
            jobs.send_backup = orig
        self.assertEqual(sent, [("x", False)])

    async def test_full_loss_restore_runs_mirrors_and_reserve_takes_over(self):
        """Сервер потерян: новая установка, из архива приезжают зеркала и запасные, токен из .env мёртв."""
        import tempfile
        from pathlib import Path
        from bot.backup import make_backup, restore_files
        app = self.app
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await app.reserves.add(TOKEN(3001))
        path = await make_backup(app)
        await app.db.execute("DELETE FROM mirrors")
        await app.db.execute("DELETE FROM reserve_tokens")
        from bot.backup import restore_backup
        await app.mirrors.stop_all()
        await restore_backup(app, path)
        await app.mirrors.start_all()
        self.assertTrue(app.mirrors.is_running(2001))
        self.assertEqual([r.bot_id for r in await app.reserves.listing()], [3001])


class MirrorBroadcast(MirrorBase):
    async def test_broadcast_reaches_mirror_users_once(self):
        h, app = self.h, self.app
        await h.send(USER, "/start")                       # человек основного бота
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await h.mirror_send(2001, OTHER, "/start")         # человек только зеркала
        mtg = self.bots.tg(2001)

        await h.admin("x:bcnew")
        await h.send(OWNER, "Новость!")
        before = len(mtg.chat(OTHER))
        await h.admin("x:bcgo")
        await app.broadcaster.task
        cur = app.broadcaster.current
        self.assertEqual(cur.via_mirrors, 1)
        self.assertEqual(self.tg.last(USER).text, "copy", "основной бот копирует как раньше")
        self.assertEqual(self.tg.chat(OTHER), [], "человеку зеркала основной бот не пишет")
        self.assertEqual(len(mtg.chat(OTHER)), before + 1)
        self.assertEqual(mtg.last(OTHER).text, "Новость!")

    async def test_mirror_block_does_not_block_main(self):
        from aiogram.exceptions import TelegramForbiddenError
        h, app = self.h, self.app
        await h.send(OTHER, "/start")
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await h.mirror_send(2001, OTHER, "/start")
        from bot.app import use_bot
        with use_bot(app.mirrors.bot_of(2001)):
            await app.mark_blocked(OTHER)
        self.assertEqual(await app.db.fetchval("SELECT is_blocked FROM users WHERE id = ?", (OTHER,)), 0)
        self.assertEqual(await app.db.fetchval(
            "SELECT is_blocked FROM user_bots WHERE user_id = ? AND bot_id = 2001", (OTHER,)), 1)


class ReserveHealth(MirrorBase):
    async def test_health_check_marks_dead_and_alerts_once(self):
        app = self.app
        await app.reserves.add(TOKEN(3001))
        await app.set_setting("reserves_min", 2)
        await app.reserves.health_check()
        alerts = [m.text for m in self.tg.chat(OWNER) if m.text and "Запасных токенов готово" in m.text]
        self.assertEqual(len(alerts), 1, "готово 1 из 2: тревога")
        await app.reserves.health_check()
        alerts = [m.text for m in self.tg.chat(OWNER) if m.text and "Запасных токенов готово" in m.text]
        self.assertEqual(len(alerts), 1, "то же состояние: повторно не тревожим")

        self.bots.tg(3001).unauthorized = True
        out = await app.reserves.health_check()
        self.assertTrue(any("отозван" in x for x in out))
        row = await app.db.fetchone("SELECT status FROM reserve_tokens WHERE bot_id = 3001")
        self.assertEqual(row["status"], "dead")

    async def test_healthy_reserves_give_no_alert(self):
        app = self.app
        await app.reserves.add(TOKEN(3001))
        await app.reserves.add(TOKEN(3002))
        await app.reserves.health_check()
        self.assertFalse([m for m in self.tg.chat(OWNER) if m.text and "Запасных токенов готово" in m.text])


class TelegramAdminMirrors(MirrorBase):
    async def test_admin_section_lists_deletes_and_checks(self):
        h, app = self.h, self.app
        await app.mirrors.add(TOKEN(2001), owner_id=USER)
        await app.reserves.add(TOKEN(3001))
        scr = await h.admin("a:mir")
        self.assertIn("Зеркал: <b>1</b> работает из 1", scr.text)
        scr = await h.admin("a:mirl")
        self.assertIn("@links2001_bot", scr.text)
        scr = await h.admin("a:mirdq:2001")
        self.assertIn("Удалить зеркало", scr.text)
        await h.admin("x:mirdel:2001")
        self.assertFalse(app.mirrors.is_running(2001))
        scr = await h.admin("x:reschk")
        self.assertIn("@links3001_bot", scr.text)
        scr = await h.admin("x:mirpub")
        self.assertIn("Создание зеркал людьми: <b>", scr.text)


class TelegramReserveAdd(MirrorBase):
    async def test_reserve_added_from_admin_chat_and_token_deleted(self):
        h, app = self.h, self.app
        await h.admin("a:resl")
        await h.admin("x:resadd")
        mid = await h.send(OWNER, TOKEN(3001))
        self.assertNotIn((OWNER, mid), self.tg.messages, "сообщение с токеном удалено")
        self.assertEqual([r.bot_id for r in await app.reserves.listing()], [3001])
        row = await app.db.fetchone("SELECT token_enc FROM reserve_tokens WHERE bot_id = 3001")
        self.assertNotIn(b"AAAAAAAA", bytes(row["token_enc"]))


class Names(unittest.TestCase):
    def test_slug_translit_and_fallback(self):
        from bot.mirrors import latin_slug
        self.assertEqual(latin_slug("Иван"), "ivan")
        self.assertEqual(latin_slug("Юлия Щ"), "yuliyasch")
        self.assertEqual(latin_slug("😀"), "user")
        self.assertEqual(latin_slug("A b-C"), "abc")
