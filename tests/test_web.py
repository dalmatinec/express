"""Веб-панель: вход, защита форм, запасные токены, зеркала, переключение и уведомление."""
import dataclasses
import re
from unittest import mock

from aiogram import Bot
from aiohttp.test_utils import TestClient, TestServer

from bot.web import build_web

from .test_mirrors import OTHER, OWNER, TOKEN, USER, MirrorBase

PASSWORD = "correct-horse-battery"


class WebPanel(MirrorBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.app.config = dataclasses.replace(self.app.config, web_password=PASSWORD)
        self.client = TestClient(TestServer(build_web(self.app)))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await super().asyncTearDown()

    async def login(self) -> None:
        r = await self.client.post("/login", data={"password": PASSWORD}, allow_redirects=False)
        self.assertEqual(r.status, 302)

    async def page(self, path: str = "/") -> str:
        r = await self.client.get(path)
        self.assertEqual(r.status, 200)
        return await r.text()

    async def csrf(self) -> str:
        return re.search(r"name=csrf value='([0-9a-f]+)'", await self.page())[1]

    async def post(self, path: str, **data):
        return await self.client.post(path, data={"csrf": await self.csrf(), **data}, allow_redirects=False)

    async def follow(self, response) -> str:
        self.assertEqual(response.status, 302)
        r = await self.client.get(response.headers["Location"])
        return await r.text()

    async def test_login_required_and_bad_password_limited(self):
        r = await self.client.get("/", allow_redirects=False)
        self.assertEqual((r.status, r.headers["Location"]), (302, "/login"))
        for _ in range(5):
            r = await self.client.post("/login", data={"password": "nope"}, allow_redirects=False)
            self.assertEqual(r.status, 401)
        r = await self.client.post("/login", data={"password": PASSWORD}, allow_redirects=False)
        self.assertEqual(r.status, 429, "после пяти неудач вход заблокирован, даже с верным паролем")

    async def test_login_ok_and_security_headers(self):
        await self.login()
        r = await self.client.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        cookie = r.request_info.headers.get("Cookie", "")
        self.assertIn("sid=", cookie)

    async def test_post_without_csrf_is_rejected(self):
        await self.login()
        r = await self.client.post("/switch/do", data={}, allow_redirects=False)
        self.assertEqual(r.status, 403)
        r = await self.client.post("/reserve/add", data={"csrf": "wrong", "token": TOKEN(3001)},
                                   allow_redirects=False)
        self.assertEqual(r.status, 403)
        self.assertEqual(await self.app.reserves.listing(), [])

    async def test_forged_session_cookie_rejected(self):
        self.client.session.cookie_jar.update_cookies({"sid": "9999999999.abcd.deadbeef"})
        r = await self.client.get("/", allow_redirects=False)
        self.assertEqual(r.status, 302)

    async def test_add_and_delete_reserve_never_shows_token(self):
        await self.login()
        text = await self.follow(await self.post("/reserve/add", token=TOKEN(3001)))
        self.assertIn("Добавлен запасной @links3001_bot", text)
        self.assertIn("@links3001_bot", text)
        self.assertNotIn(TOKEN(3001), text)
        self.assertNotIn("A" * 35, text)

        text = await self.follow(await self.post("/reserve/add", token="мусор"))
        self.assertIn("Не добавлен", text)

        text = await self.follow(await self.post("/reserve/delete", bot_id="3001"))
        self.assertIn("Запасной удалён", text)
        self.assertEqual(await self.app.reserves.listing(), [])

    async def test_reserve_rights_warning_shown(self):
        await self.h.make_item()
        self.bots.tg(3001).dead_chats.add(-1001)
        await self.login()
        text = await self.follow(await self.post("/reserve/add", token=TOKEN(3001)))
        self.assertIn("не готов в каналах", text)

    async def test_mirrors_listing_escapes_html_and_delete(self):
        await self.app.mirrors.add(TOKEN(2001), USER)
        await self.app.db.execute("UPDATE mirrors SET username = '<script>alert(1)</script>' WHERE bot_id = 2001")
        await self.login()
        text = await self.page()
        self.assertNotIn("<script>alert", text)
        self.assertIn("&lt;script&gt;", text)
        text = await self.follow(await self.post("/mirror/delete", bot_id="2001"))
        self.assertIn("Зеркало удалено", text)
        self.assertFalse(self.app.mirrors.is_running(2001))
        text = await self.follow(await self.post("/mirror/delete", bot_id="abc"))
        self.assertIn("не найдено", text)

    async def test_toggle_public_mirrors(self):
        await self.login()
        self.assertTrue(self.app.mirrors.public)
        await self.post("/mirror/public")
        self.assertFalse(self.app.mirrors.public)
        await self.h.send(USER, "/start")
        buttons = [b.callback_data for row in self.h.user_screen(USER).markup.inline_keyboard for b in row] \
            if self.h.user_screen(USER).markup else []
        self.assertNotIn("mir", buttons)

    async def test_manual_switch_with_confirmation(self):
        h, app = self.h, self.app
        await app.reserves.add(TOKEN(3001))
        h.start_runner()
        await self.login()
        r = await self.post("/switch/ask")
        self.assertEqual(r.status, 200)
        self.assertIn("Переключить основной бот?", await r.text())
        self.assertEqual(app.main_bot.id, 1000, "пока не подтвердили, ничего не изменилось")
        text = await self.follow(await self.post("/switch/do"))
        self.assertIn("теперь @links3001_bot", text)
        self.assertEqual(app.main_bot.id, 3001)
        text = await self.page()
        self.assertIn("Последнее переключение (вручную)", text)

    async def test_notify_asks_confirmation_then_sends_to_mirror_users(self):
        h, app = self.h, self.app
        await app.mirrors.add(TOKEN(2001), USER)
        await h.mirror_send(2001, OTHER, "/start")
        await h.send(USER, "/start")
        await self.login()
        r = await self.post("/notify/ask", text="Мы переехали <b>сюда</b>")
        body = await r.text()
        self.assertIn("Получат: <b>2</b>", body)
        self.assertIn("&lt;b&gt;сюда&lt;/b&gt;", body, "разметку из текста экранируем")
        self.assertIsNone(app.notifier.state, "без подтверждения ничего не ушло")

        text = await self.follow(await self.post("/notify/send", text="Мы переехали"))
        self.assertIn("Рассылка запущена", text)
        await app.notifier.wait()
        self.assertEqual(app.notifier.state.sent, 2)
        self.assertEqual(self.bots.tg(2001).last(OTHER).text, "Мы переехали")
        self.assertEqual(self.tg.last(USER).text, "Мы переехали")

    async def test_blocked_user_is_counted_and_skipped_next_time(self):
        from aiogram.exceptions import TelegramForbiddenError
        h, app = self.h, self.app
        await app.mirrors.add(TOKEN(2001), USER)
        await h.mirror_send(2001, OTHER, "/start")

        async def forbid(*a, **k):
            raise TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
        with mock.patch.object(Bot, "send_message", forbid):
            app.notifier.start("тест")
            await app.notifier.wait()
        self.assertEqual((app.notifier.state.sent, app.notifier.state.blocked), (0, 1))
        row = await app.db.fetchone("SELECT is_blocked FROM user_bots WHERE user_id = ? AND bot_id = 2001", (OTHER,))
        self.assertEqual(row["is_blocked"], 1)
        app.notifier.start("тест")
        await app.notifier.wait()
        self.assertEqual(app.notifier.state.total, 0, "заблокировавших больше не трогаем")

    async def test_panel_without_secret_key_warns(self):
        self.app.cipher = None
        await self.login()
        self.assertIn("SECRET_KEY не задан", await self.page())

