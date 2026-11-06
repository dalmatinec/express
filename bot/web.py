"""Веб-панель: запасные токены, зеркала, переключение основного бота, уведомление зеркалам.

Включается, если в .env заданы WEB_PORT и WEB_PASSWORD (не короче 8 знаков). Слушает только 127.0.0.1:
наружу её выставляют через SSH-туннель или обратный прокси с HTTPS. Токены в панели не показываются:
их можно только добавить и удалить.

Защита: пароль, подписанная сессия в HttpOnly-cookie, проверка CSRF на каждой форме,
ограничение попыток входа, заголовки против встраивания и кэширования."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from html import escape
from urllib.parse import quote

from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from .app import App
from .mirrors import MirrorError
from .notify import default_text
from .store import now

log = logging.getLogger(__name__)

SESSION_TTL = 12 * 3600
LOGIN_WINDOW = 300
LOGIN_MAX_FAILS = 5
MIN_PASSWORD = 8

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1c1f24;--muted:#667085;--line:#e4e7ec;--accent:#2563eb;--bad:#d92d20;--ok:#12b76a;--warn:#f79009}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--text:#e8eaee;--muted:#98a2b3;--line:#2a2f3a;--accent:#5b8cff;--bad:#f97066;--ok:#32d583;--warn:#fdb022}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}
.muted{color:var(--muted);font-size:13px}table{width:100%;border-collapse:collapse}
td,th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:14px}th{color:var(--muted);font-weight:500}
input[type=text],input[type=password],textarea{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font:inherit}
textarea{min-height:90px}button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.danger{color:var(--bad);border-color:var(--bad)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row>input{flex:1;min-width:200px}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;border:1px solid var(--line)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.flash{padding:10px 14px;border-radius:10px;background:var(--card);border:1px solid var(--accent);margin:12px 0}
form.inline{display:inline;margin:0}nav{display:flex;justify-content:space-between;align-items:center}
"""

STATUS_RU = {"ready": "готов", "active": "работает сейчас", "dead": "не рабочий"}


def e(value: object) -> str:
    return escape(str(value), quote=True)


def page(title: str, body: str, nav: str = "") -> web.Response:
    html = (f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{CSS}</style></head><body><main>{nav}{body}</main></body></html>")
    return web.Response(text=html, content_type="text/html")


def fmt_ts(ts: int) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


class Panel:
    def __init__(self, app: App) -> None:
        self.app = app
        secret = (app.config.secret_key or secrets.token_hex(32)).encode()
        self._key = hashlib.sha256(b"web-panel:" + secret).digest()
        self._fails: dict[str, list[float]] = {}

    # ---------- сессия и CSRF ----------
    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def new_session(self) -> str:
        payload = f"{int(time.time()) + SESSION_TTL}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def valid_session(self, cookie: str | None) -> bool:
        if not cookie or cookie.count(".") != 2:
            return False
        exp, nonce, sig = cookie.split(".")
        if not exp.isdigit() or int(exp) < time.time():
            return False
        return hmac.compare_digest(sig, self._sign(f"{exp}.{nonce}"))

    def csrf(self, cookie: str) -> str:
        return self._sign("csrf:" + cookie)

    def _too_many_fails(self, ip: str) -> bool:
        t = time.time()
        recent = [x for x in self._fails.get(ip, []) if t - x < LOGIN_WINDOW]
        self._fails[ip] = recent
        return len(recent) >= LOGIN_MAX_FAILS

    # ---------- middleware ----------
    @web.middleware
    async def guard(self, request: web.Request, handler):
        if request.path != "/login":
            cookie = request.cookies.get("sid")
            if not self.valid_session(cookie):
                raise web.HTTPFound("/login")
            if request.method == "POST":
                data = await request.post()
                token = str(data.get("csrf", ""))
                if not hmac.compare_digest(token, self.csrf(cookie or "")):
                    raise web.HTTPForbidden(text="Сессия устарела, обновите страницу.")
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        return response

    # ---------- вход ----------
    async def login_page(self, request: web.Request) -> web.Response:
        if request.method == "POST":
            ip = request.remote or "?"
            if self._too_many_fails(ip):
                return self._login_form("Слишком много попыток, подождите несколько минут.", status=429)
            data = await request.post()
            password = str(data.get("password", "")).encode()
            if hmac.compare_digest(password, self.app.config.web_password.encode()):
                response = web.HTTPFound("/")
                secure = request.secure or request.headers.get("X-Forwarded-Proto") == "https"
                response.set_cookie("sid", self.new_session(), max_age=SESSION_TTL, httponly=True,
                                    samesite="Strict", secure=secure, path="/")
                raise response
            self._fails.setdefault(ip, []).append(time.time())
            return self._login_form("Неверный пароль.", status=401)
        return self._login_form()

    def _login_form(self, error: str = "", status: int = 200) -> web.Response:
        err = f"<p class=bad>{e(error)}</p>" if error else ""
        body = ("<div class=card><h1>Вход</h1>" + err +
                "<form method=post><p><input type=password name=password placeholder='Пароль' autofocus "
                "autocomplete=current-password></p><button class=primary>Войти</button></form></div>")
        response = page("Вход", body)
        response.set_status(status)
        return response

    async def logout(self, request: web.Request) -> web.Response:
        response = web.HTTPFound("/login")
        response.del_cookie("sid", path="/")
        raise response

    # ---------- главная ----------
    def _form(self, request: web.Request, action: str, inner: str, extra: str = "", cls: str = "inline") -> str:
        token = self.csrf(request.cookies.get("sid", ""))
        return (f"<form method=post action='{action}' class={cls}>"
                f"<input type=hidden name=csrf value='{token}'>{extra}{inner}</form>")

    async def index(self, request: web.Request) -> web.Response:
        app = self.app
        flash = request.query.get("m", "")[:300]
        runner = app.runner
        down = bool(runner and runner.state.down)
        status = ("<span class='pill bad'>не работает, запасных нет</span>" if down
                  else "<span class='pill ok'>работает</span>")
        last = ""
        if runner and runner.state.last:
            f = runner.state.last
            kind = "вручную" if f.manual else "автоматически"
            last = (f"<p class=muted>Последнее переключение ({kind}) {fmt_ts(f.ts)}: "
                    f"@{e(f.old_username)} → @{e(f.new_username)}. Причина: {e(f.reason)}</p>")

        parts = [f"<nav><div><h1>Панель бота</h1><div class=muted>Основной бот</div></div>"
                 f"{self._form(request, '/logout', '<button>Выйти</button>')}</nav>"]
        if flash:
            parts.append(f"<div class=flash>{e(flash)}</div>")
        if app.cipher is None:
            parts.append("<div class=card><b class=warn>SECRET_KEY не задан.</b> Запасные токены и зеркала "
                         "выключены. Добавьте SECRET_KEY в .env и перезапустите бот.</div>")
        parts.append(f"<div class=card><h2>@{e(app.bot_username)} {status}</h2>{last}</div>")
        parts.append(await self._reserves_block(request))
        parts.append(await self._mirrors_block(request))
        parts.append(self._notify_block(request))
        return page("Панель бота", "".join(parts))

    async def _reserves_block(self, request: web.Request) -> str:
        app = self.app
        rows = ""
        for r in await app.reserves.listing():
            cls = {"active": "ok", "dead": "bad"}.get(r.status, "")
            action = ""
            if r.status != "active":
                action = self._form(request, "/reserve/delete",
                                    "<button class=danger>Удалить</button>",
                                    f"<input type=hidden name=bot_id value='{r.bot_id}'>")
            rows += (f"<tr><td>{r.position}</td><td>@{e(r.username)}</td>"
                     f"<td class={cls}>{e(STATUS_RU.get(r.status, r.status))}</td><td>{action}</td></tr>")
        table = (f"<table><tr><th>№</th><th>Бот</th><th>Статус</th><th></th></tr>{rows}</table>" if rows
                 else "<p class=muted>Запасных пока нет.</p>")
        add = ""
        if app.cipher is not None:
            add = self._form(
                request, "/reserve/add",
                "<button class=primary>Добавить</button>",
                "<input type=password name=token placeholder='Токен запасного бота' autocomplete=off>",
                cls="row")
        switch = ""
        if app.runner is not None and rows:
            switch = self._form(request, "/switch/ask", "<button>Переключить на запасной сейчас</button>")
        return ("<div class=card><h2>Запасные токены</h2>"
                "<p class=muted>Если основной бот перестанет отвечать, автоматически включится первый "
                "готовый запасной. Запасной бот заранее добавьте админом во все каналы с правом "
                "«Приглашать пользователей». Токены здесь не показываются.</p>"
                f"{table}<p>{add}</p><p>{switch}</p></div>")

    async def _mirrors_block(self, request: web.Request) -> str:
        app = self.app
        rows = ""
        for m in await app.mirrors.listing():
            state = ("<span class=ok>работает</span>" if m["running"]
                     else "<span class=bad>отключено</span>")
            delete = self._form(request, "/mirror/delete", "<button class=danger>Удалить</button>",
                                f"<input type=hidden name=bot_id value='{m['bot_id']}'>")
            rows += (f"<tr><td>@{e(m['username'])}</td><td>{m['owner_id']}</td><td>{state}</td>"
                     f"<td>{m['users']}</td><td>{delete}</td></tr>")
        table = (f"<table><tr><th>Зеркало</th><th>Владелец</th><th>Статус</th><th>Людей</th><th></th></tr>{rows}</table>"
                 if rows else "<p class=muted>Зеркал пока нет.</p>")
        public = bool(app.store.setting("mirrors_public", 1))
        toggle = self._form(
            request, "/mirror/public",
            f"<button>{'Выключить' if public else 'Включить'} создание зеркал людьми</button>")
        return (f"<div class=card><h2>Зеркала</h2>{table}"
                f"<p class=muted>Создание людьми сейчас: <b>{'включено' if public else 'выключено'}</b></p>"
                f"<p>{toggle}</p></div>")

    def _notify_block(self, request: web.Request) -> str:
        app = self.app
        status = ""
        if app.notifier.state is not None:
            status = f"<p class=muted>Последняя рассылка: {e(app.notifier.state.summary())}</p>"
        form = self._form(
            request, "/notify/ask", "<p><button class=primary>Проверить и отправить</button></p>",
            f"<p><textarea name=text maxlength=3500>{e(default_text(app.bot_username))}</textarea></p>",
            cls="")
        return ("<div class=card><h2>Уведомление зеркалам</h2>"
                "<p class=muted>Сообщение получат люди, которые запускали основной бот или зеркала "
                "и не заблокировали их. Telegram не даёт боту писать первым, поэтому остальных "
                "не достать.</p>" + status + form + "</div>")

    # ---------- действия ----------
    def _back(self, message: str) -> web.HTTPFound:
        return web.HTTPFound("/?m=" + quote(message))

    async def reserve_add(self, request: web.Request) -> web.Response:
        data = await request.post()
        try:
            reserve, problems = await self.app.reserves.add(str(data.get("token", "")))
        except MirrorError as ex:
            raise self._back(f"Не добавлен: {ex}")
        await self.app.log_action(0, "web_reserve_add", reserve.username,
                                  f"Веб-панель: добавлен запасной @{reserve.username}")
        note = "Добавлен запасной @" + reserve.username
        if problems:
            note += ". Внимание, он не готов в каналах: " + "; ".join(problems[:5])
        raise self._back(note)

    async def reserve_delete(self, request: web.Request) -> web.Response:
        data = await request.post()
        bot_id = str(data.get("bot_id", ""))
        if not bot_id.isdigit() or not await self.app.reserves.remove(int(bot_id)):
            raise self._back("Не удалось удалить: работающий основной удалить нельзя.")
        await self.app.log_action(0, "web_reserve_delete", bot_id, f"Веб-панель: удалён запасной {bot_id}")
        raise self._back("Запасной удалён.")

    async def switch_ask(self, request: web.Request) -> web.Response:
        ready = await self.app.reserves.candidates()
        if not ready:
            raise self._back("Нет готовых запасных токенов.")
        body = ("<div class=card><h2>Переключить основной бот?</h2>"
                f"<p>Сейчас работает @{e(self.app.bot_username)}. Бот сразу заменится на первого готового "
                "запасного. Люди, пользовавшиеся прежним, останутся без бота, пока не получат уведомление.</p>"
                + self._form(request, "/switch/do", "<button class=danger>Да, переключить</button>")
                + " <a href='/'>Нет</a></div>")
        return page("Подтверждение", body)

    async def switch_do(self, request: web.Request) -> web.Response:
        runner = self.app.runner
        if runner is None:
            raise self._back("Переключение недоступно.")
        info = await runner.failover("вручную из панели", manual=True)
        if info is None:
            raise self._back("Переключиться не на что: живых запасных нет.")
        await self.app.log_action(0, "web_switch", info.new_username,
                                  f"Веб-панель: основной бот переключён на @{info.new_username}")
        raise self._back(f"Основной бот теперь @{info.new_username}. Не забудьте отправить уведомление зеркалам.")

    async def mirror_delete(self, request: web.Request) -> web.Response:
        data = await request.post()
        bot_id = str(data.get("bot_id", ""))
        if not bot_id.isdigit() or not await self.app.mirrors.remove(int(bot_id)):
            raise self._back("Зеркало не найдено.")
        await self.app.log_action(0, "web_mirror_delete", bot_id, f"Веб-панель: удалено зеркало {bot_id}")
        raise self._back("Зеркало удалено.")

    async def mirror_public(self, request: web.Request) -> web.Response:
        value = 0 if self.app.store.setting("mirrors_public", 1) else 1
        await self.app.set_setting("mirrors_public", value)
        raise self._back("Создание зеркал людьми " + ("включено." if value else "выключено."))

    async def notify_ask(self, request: web.Request) -> web.Response:
        app = self.app
        data = await request.post()
        text = str(data.get("text", "")).strip()[:3500]
        if not text:
            raise self._back("Текст пустой.")
        bots = [app.main_bot, *app.mirrors.running_bots()]
        total = 0
        for bot in bots:
            total += await app.db.fetchval(
                "SELECT COUNT(*) FROM user_bots WHERE bot_id = ? AND is_blocked = 0", (bot.id,))
        body = ("<div class=card><h2>Отправить уведомление?</h2>"
                f"<p>Получат: <b>{total}</b> человек через {len(bots)} бот(ов): основной и работающие зеркала.</p>"
                f"<div class=card style='white-space:pre-wrap'>{e(text)}</div>"
                + self._form(request, "/notify/send", "<button class=primary>Да, отправить</button>",
                             f"<input type=hidden name=text value='{e(text)}'>")
                + " <a href='/'>Нет</a></div>")
        return page("Подтверждение", body)

    async def notify_send(self, request: web.Request) -> web.Response:
        data = await request.post()
        text = str(data.get("text", "")).strip()[:3500]
        if not text or not self.app.notifier.start(text):
            raise self._back("Рассылка уже идёт или текст пустой.")
        await self.app.log_action(0, "web_notify", "", "Веб-панель: запущено уведомление зеркалам")
        raise self._back("Рассылка запущена. Ход виден в блоке ниже после обновления страницы.")


def build_web(app: App) -> web.Application:
    panel = Panel(app)
    site = web.Application(middlewares=[panel.guard])
    site.add_routes([
        web.route("*", "/login", panel.login_page),
        web.post("/logout", panel.logout),
        web.get("/", panel.index),
        web.post("/reserve/add", panel.reserve_add),
        web.post("/reserve/delete", panel.reserve_delete),
        web.post("/switch/ask", panel.switch_ask),
        web.post("/switch/do", panel.switch_do),
        web.post("/mirror/delete", panel.mirror_delete),
        web.post("/mirror/public", panel.mirror_public),
        web.post("/notify/ask", panel.notify_ask),
        web.post("/notify/send", panel.notify_send),
    ])
    return site


async def start_web(app: App) -> web.AppRunner | None:
    cfg = app.config
    if len(cfg.web_password) < MIN_PASSWORD:
        log.error("Веб-панель не запущена: WEB_PASSWORD должен быть не короче %s знаков", MIN_PASSWORD)
        return None
    runner = web.AppRunner(build_web(app), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, cfg.web_host, cfg.web_port).start()
    log.info("Веб-панель: http://%s:%s", cfg.web_host, cfg.web_port)
    return runner
