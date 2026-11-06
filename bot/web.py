"""Веб-панель: запасные токены, зеркала, переключение основного бота, уведомление зеркалам.

Включается, если в .env заданы WEB_PORT и WEB_PASSWORD (не короче 8 знаков). Слушает только 127.0.0.1:
наружу её выставляют через SSH-туннель или обратный прокси с HTTPS. Токены в панели не показываются:
их можно только добавить и удалить.

Защита: пароль, подписанная сессия в HttpOnly-cookie, проверка CSRF на каждой форме,
ограничение попыток входа, заголовки против встраивания и кэширования."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
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
:root{--bg:#f4f5f8;--card:#fff;--text:#161a22;--muted:#6b7280;--line:#e5e7ee;--accent:#4f46e5;--accent2:#7c3aed;--bad:#dc2626;--ok:#16a34a;--warn:#d97706;--soft:#eef0f6;--shadow:0 1px 2px rgba(16,24,40,.05),0 6px 20px rgba(16,24,40,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#0d0f14;--card:#161922;--text:#e9ebf1;--muted:#98a1b3;--line:#262b38;--accent:#818cf8;--accent2:#a78bfa;--bad:#f87171;--ok:#4ade80;--warn:#fbbf24;--soft:#1d2130;--shadow:0 1px 2px rgba(0,0,0,.4)}}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:0 16px 72px}
header.top{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:18px 0 6px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px}.logo{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:grid;place-items:center;color:#fff;font-size:20px}
h1{font-size:20px;margin:0;line-height:1.2}h2{font-size:16px;margin:0 0 4px}h3{font-size:14px;margin:14px 0 6px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 4px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:5}
.tabs a{padding:7px 14px;border-radius:99px;background:var(--card);border:1px solid var(--line);color:var(--text);text-decoration:none;font-size:14px}
.tabs a:hover{border-color:var(--accent);color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin:14px 0;box-shadow:var(--shadow)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(138px,1fr));gap:12px;margin:14px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:var(--shadow)}
.stat b{display:block;font-size:26px;line-height:1.15}.stat span{color:var(--muted);font-size:13px}
.muted{color:var(--muted);font-size:13px}.hint{background:var(--soft);border-radius:10px;padding:10px 12px;color:var(--muted);font-size:13px;margin:8px 0 12px}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:420px}
td,th{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:middle}th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:0}
input[type=text],input[type=password],textarea{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--text);font:inherit}
input:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px}textarea{min-height:110px;resize:vertical}
button{font:inherit;font-weight:500;padding:9px 16px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer;transition:.12s}
button:hover{border-color:var(--accent);color:var(--accent)}
button.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));border-color:transparent;color:#fff}button.primary:hover{color:#fff;filter:brightness(1.08)}
button.danger{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,var(--line))}button.danger:hover{background:var(--bad);color:#fff;border-color:var(--bad)}
button.small{padding:5px 11px;font-size:13px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row>input{flex:1;min-width:200px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:500;background:var(--soft)}
.pill:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.pill.ok{color:var(--ok)}.pill.bad{color:var(--bad)}.pill.warn{color:var(--warn)}.pill.off{color:var(--muted)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.flash{padding:12px 16px;border-radius:12px;background:var(--soft);border-left:4px solid var(--accent);margin:14px 0}
.alert{padding:12px 16px;border-radius:12px;background:color-mix(in srgb,var(--warn) 14%,var(--card));border:1px solid color-mix(in srgb,var(--warn) 40%,var(--line));margin:14px 0}
form.inline{display:inline;margin:0}.log li{padding:6px 0;border-bottom:1px solid var(--line);font-size:14px;list-style:none}.log{padding:0;margin:0}.log time{color:var(--muted);font-size:12px;margin-right:8px}
.center{max-width:380px;margin:12vh auto 0}.center .logo{margin:0 auto 14px;width:54px;height:54px;font-size:26px}
@media (max-width:560px){h1{font-size:18px}.stat b{font-size:22px}td,th{padding:8px 5px}}
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
        self._warned: set[str] = set()  # адреса, о блокировке которых уже сообщили
        self._codes: dict[int, list] = {}  # tg_id -> [хэш кода, до какого времени, попыток]
        self._code_asks: dict[str, list[float]] = {}  # ip или id -> когда просили код
        self._tasks: set[asyncio.Task] = set()

    # ---------- сессия и CSRF ----------
    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def new_session(self, uid: int = 0) -> str:
        """uid: Telegram ID админа, вошедшего по коду; 0 - вход общим паролем (права владельца)."""
        payload = f"{int(time.time()) + SESSION_TTL}.{uid}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def session_uid(self, cookie: str | None) -> int | None:
        if not cookie or cookie.count(".") != 3:
            return None
        exp, uid, nonce, sig = cookie.split(".")
        if not exp.isdigit() or not uid.isdigit() or int(exp) < time.time():
            return None
        if not hmac.compare_digest(sig, self._sign(f"{exp}.{uid}.{nonce}")):
            return None
        return int(uid)

    def valid_session(self, cookie: str | None) -> bool:
        return self.session_uid(cookie) is not None

    def is_owner(self, uid: int) -> bool:
        return uid == 0 or uid in self.app.config.owner_ids

    def _uid(self, request: web.Request) -> int:
        return int(request.get("uid", 0))

    def _need_owner(self, request: web.Request) -> None:
        if not self.is_owner(self._uid(request)):
            raise web.HTTPForbidden(text="Это действие доступно только владельцам бота.")

    @staticmethod
    def ip_of(request: web.Request) -> str:
        """Адрес клиента. За прокси (Caddy на этом же сервере) берём то, что дописал прокси."""
        remote = request.remote or "?"
        if remote in ("127.0.0.1", "::1"):
            fwd = request.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[-1].strip() or remote
        return remote

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
            uid = self.session_uid(cookie)
            if uid is None:
                raise web.HTTPFound("/login")
            if not self.is_owner(uid) and not self.app.has_perm(uid, "mirrors"):
                raise web.HTTPFound("/login")  # права сняли - сессия перестаёт работать сразу
            request["uid"] = uid
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
        if request.headers.get("X-Forwarded-Proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        return response

    # ---------- вход ----------
    def _tell_owners(self, html: str) -> None:
        """Сообщение владельцам о событии входа. Не ждём отправки, чтобы вход не тормозил."""
        async def send() -> None:
            for uid in sorted(self.app.config.owner_ids):
                try:
                    await self.app.main_bot.send_message(uid, html)
                except Exception:  # noqa: BLE001 - уведомление не должно ломать вход
                    pass
        task = asyncio.get_running_loop().create_task(send())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _asks_exceeded(self, key: str, limit: int) -> bool:
        t = time.time()
        recent = [x for x in self._code_asks.get(key, []) if t - x < LOGIN_WINDOW]
        recent.append(t)
        self._code_asks[key] = recent
        return len(recent) > limit

    async def login_page(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            return self._login_form()
        ip = self.ip_of(request)
        if self._too_many_fails(ip):
            if ip not in self._warned:
                self._warned.add(ip)
                self._tell_owners(f"🚨 Веб-панель: вход с адреса <code>{e(ip)}</code> заблокирован после "
                                  f"{LOGIN_MAX_FAILS} неверных попыток.")
            return self._login_form("Слишком много попыток, подождите несколько минут.", status=429)
        data = await request.post()
        if "code" in data:
            return await self._verify_code(request, ip, data)
        if "tg_id" in data:
            return await self._send_code(ip, data)
        password = str(data.get("password", "")).encode()
        if hmac.compare_digest(password, self.app.config.web_password.encode()):
            return self._enter(request, ip, 0, "общий пароль")
        self._fails.setdefault(ip, []).append(time.time())
        return self._login_form("Неверный пароль.", status=401)

    def _enter(self, request: web.Request, ip: str, uid: int, how: str) -> web.Response:
        self._warned.discard(ip)
        self._tell_owners(f"🔑 Вход в веб-панель ({e(how)}) с адреса <code>{e(ip)}</code>. "
                          "Если это не вы, смените WEB_PASSWORD и проверьте список админов.")
        response = web.HTTPFound("/")
        secure = request.secure or request.headers.get("X-Forwarded-Proto") == "https"
        response.set_cookie("sid", self.new_session(uid), max_age=SESSION_TTL, httponly=True,
                            samesite="Strict", secure=secure, path="/")
        raise response

    async def _send_code(self, ip: str, data) -> web.Response:
        raw = str(data.get("tg_id", "")).strip()
        if not raw.isdigit() or len(raw) > 15:
            return self._login_form("Telegram ID состоит из цифр.", status=400)
        uid = int(raw)
        if self._asks_exceeded("ip:" + ip, 6) or self._asks_exceeded(f"id:{uid}", 3):
            return self._login_form("Слишком много запросов кода, подождите несколько минут.", status=429)
        app = self.app
        if uid in app.admin_ids() and (self.is_owner(uid) or app.has_perm(uid, "mirrors")):
            code = f"{secrets.randbelow(10 ** 6):06d}"
            self._codes[uid] = [self._sign(f"code:{uid}:{code}"), time.time() + 300, 0]
            try:
                await app.main_bot.send_message(
                    uid, f"🔐 Код для входа в веб-панель: <code>{code}</code>\n"
                         f"Запрос с адреса {e(ip)}. Действует 5 минут. Никому его не сообщайте.")
            except TelegramAPIError:
                pass  # не писали боту - достучаться нельзя, ответ ниже всё равно одинаковый
        # ответ одинаковый для всех: так нельзя выяснить, кто админ
        return self._code_form(raw)

    async def _verify_code(self, request: web.Request, ip: str, data) -> web.Response:
        raw = str(data.get("tg_id", "")).strip()
        code = str(data.get("code", "")).strip()
        uid = int(raw) if raw.isdigit() and len(raw) <= 15 else -1
        entry = self._codes.get(uid)
        ok = False
        if entry is not None and entry[1] >= time.time() and entry[2] < 5:
            entry[2] += 1
            ok = hmac.compare_digest(entry[0], self._sign(f"code:{uid}:{code}"))
        if ok:
            self._codes.pop(uid, None)
            return self._enter(request, ip, uid, f"код из Telegram, ID {uid}")
        self._fails.setdefault(ip, []).append(time.time())
        return self._code_form(raw, "Неверный или просроченный код.", status=401)

    def _code_form(self, tg_id: str, error: str = "", status: int = 200) -> web.Response:
        err = f"<p class=bad>{e(error)}</p>" if error else ""
        body = ("<div class='card center' style='text-align:center'><div class=logo>💬</div><h1>Введите код</h1>"
                "<p class=muted>Если ваш ID есть в списке админов, бот прислал код в Telegram.</p>" + err +
                f"<form method=post><input type=hidden name=tg_id value='{e(tg_id)}'>"
                "<p><input type=text name=code placeholder='6 цифр' inputmode=numeric autocomplete=one-time-code "
                "autofocus maxlength=6></p><button class=primary style='width:100%'>Войти</button></form>"
                "<p><a href='/login'>Назад</a></p></div>")
        response = page("Код", body)
        response.set_status(status)
        return response

    def _login_form(self, error: str = "", status: int = 200) -> web.Response:
        err = f"<p class=bad>{e(error)}</p>" if error else ""
        body = ("<div class='card center' style='text-align:center'><div class=logo>🔐</div><h1>Вход в панель</h1>"
                "<p class=muted>Введите свой Telegram ID: бот пришлёт код. ID можно узнать у @userinfobot.</p>" + err +
                "<form method=post><p><input type=text name=tg_id placeholder='Ваш Telegram ID' inputmode=numeric "
                "autofocus></p><button class=primary style='width:100%'>Получить код</button></form>"
                "<details style='margin-top:18px'><summary class=muted>Войти общим паролем (владелец)</summary>"
                "<form method=post><p><input type=password name=password placeholder='Пароль' "
                "autocomplete=current-password></p><button style='width:100%'>Войти</button></form></details></div>")
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

        parts = [
            "<header class=top><div class=brand><div class=logo>🔗</div>"
            f"<div><h1>Панель бота</h1><div class=muted>@{e(app.bot_username)} {status}</div></div></div>"
            f"{self._form(request, '/logout', '<button class=small>Выйти</button>')}</header>"
            "<div class=tabs><a href='#reserves'>Запасные</a><a href='#mirrors'>Зеркала</a>"
            "<a href='#notify'>Уведомление</a><a href='#tools'>Сервис</a><a href='#events'>События</a></div>"]
        if flash:
            parts.append(f"<div class=flash>{e(flash)}</div>")
        if app.cipher is None:
            parts.append("<div class=alert><b>SECRET_KEY не задан.</b> Запасные токены и зеркала "
                         "выключены. Добавьте SECRET_KEY в .env и перезапустите бот.</div>")
        if down:
            parts.append("<div class=alert><b>Основной бот не работает, запасных нет.</b> Добавьте запасной токен ниже: "
                         "бот сам подхватит его в течение минуты.</div>")
        if last:
            parts.append(f"<div class=card>{last}</div>")
        parts.append(await self._stats_block())
        parts.append(await self._reserves_block(request))
        parts.append(await self._mirrors_block(request))
        if self.is_owner(self._uid(request)):
            parts.append(self._notify_block(request))
        parts.append(self._tools_block(request))
        parts.append(await self._events_block())
        return page("Панель бота", "".join(parts))

    async def _stats_block(self) -> str:
        app, db = self.app, self.app.db
        day = now() - 86400
        users = await db.fetchval("SELECT COUNT(*) FROM users")
        fresh = await db.fetchval("SELECT COUNT(*) FROM users WHERE created_at >= ?", (day,))
        active = await db.fetchval("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (day,))
        mirrors = await app.mirrors.listing() if app.mirrors is not None else []
        alive = sum(1 for m in mirrors if m["running"])
        ready = sum(1 for r in await app.reserves.listing() if r.status == "ready")
        pool = sum(app.links.pool_counts.values())
        cells = [(users, "людей всего"), (fresh, "новых за сутки"), (active, "активных за сутки"),
                 (f"{alive}/{len(mirrors)}", "зеркал работает"), (ready, "запасных готово"), (pool, "ссылок в запасе")]
        return "<div class=stats>" + "".join(f"<div class=stat><b>{v}</b><span>{t}</span></div>" for v, t in cells) + "</div>"

    def _tools_block(self, request: web.Request) -> str:
        return ("<div class=card id=tools><h2>Сервис</h2>"
                "<p class=muted>Бэкап уходит в канал логов (без него владельцам в личку). Проверка запасных "
                "идёт сама раз в час, здесь можно запустить вручную.</p><div class=actions>"
                + (self._form(request, "/backup/now", "<button>💾 Сделать бэкап</button>")
                   if self.is_owner(self._uid(request)) else "")
                + self._form(request, "/reserve/check", "<button>🩺 Проверить запасных</button>")
                + "</div></div>")

    async def _events_block(self) -> str:
        rows = await self.app.db.fetchall("SELECT ts, action, details FROM admin_log ORDER BY id DESC LIMIT 10")
        items = "".join(f"<li><time>{fmt_ts(r['ts'])}</time>{e(r['action'])} {e(r['details'] or '')}</li>" for r in rows)
        return f"<div class=card id=events><h2>Последние события</h2><ul class=log>{items or '<li>пусто</li>'}</ul></div>"

    async def _reserves_block(self, request: web.Request) -> str:
        app = self.app
        rows = ""
        for r in await app.reserves.listing():
            cls = {"active": "ok", "dead": "bad", "ready": "ok"}.get(r.status, "warn")
            if r.status == "ready" and app.reserves.problems.get(r.bot_id):
                cls = "warn"
            action = ""
            if r.status != "active":
                action = self._form(request, "/reserve/delete",
                                    "<button class='danger small'>Удалить</button>",
                                    f"<input type=hidden name=bot_id value='{r.bot_id}'>")
            issues = app.reserves.problems.get(r.bot_id)
            note = f"<div class='muted warn'>{e('; '.join(issues[:3]))}</div>" if issues else ""
            rows += (f"<tr><td>{r.position}</td><td>@{e(r.username)}{note}</td>"
                     f"<td><span class='pill {cls}'>{e(STATUS_RU.get(r.status, r.status))}</span></td>"
                     f"<td>{action}</td></tr>")
        table = (f"<div class=scroll><table><tr><th>№</th><th>Бот</th><th>Статус</th><th></th></tr>{rows}</table></div>"
                 if rows else "<p class=muted>Запасных пока нет.</p>")
        add = ""
        if app.cipher is not None:
            add = self._form(
                request, "/reserve/add",
                "<button class=primary>Добавить</button>",
                "<input type=password name=token placeholder='Токен запасного бота' autocomplete=off>",
                cls="row")
        switch = ""
        if app.runner is not None and rows and self.is_owner(self._uid(request)):
            switch = self._form(request, "/switch/ask", "<button>Переключить на запасной сейчас</button>")
        return ("<div class=card id=reserves><h2>Запасные токены</h2>"
                "<div class=hint>Если основной бот перестанет отвечать, автоматически включится первый "
                "готовый запасной. Запасной бот заранее добавьте админом во все каналы с правом "
                "«Приглашать пользователей». Токены здесь не показываются.</div>"
                f"{table}<p>{add}</p><p>{switch}</p></div>")

    async def _mirrors_block(self, request: web.Request) -> str:
        app = self.app
        rows = ""
        for m in await app.mirrors.listing():
            state = ("<span class='pill ok'>работает</span>" if m["running"]
                     else "<span class='pill bad'>отключено</span>")
            delete = self._form(request, "/mirror/delete", "<button class='danger small'>Удалить</button>",
                                f"<input type=hidden name=bot_id value='{m['bot_id']}'>")
            rows += (f"<tr><td>@{e(m['username'])}</td><td>{m['owner_id']}</td><td>{state}</td>"
                     f"<td>{m['users']}</td><td>{delete}</td></tr>")
        table = (f"<div class=scroll><table><tr><th>Зеркало</th><th>Владелец</th><th>Статус</th><th>Людей</th><th></th></tr>{rows}</table></div>"
                 if rows else "<p class=muted>Зеркал пока нет.</p>")
        public = bool(app.store.setting("mirrors_public", 1))
        toggle = self._form(
            request, "/mirror/public",
            f"<button>{'Выключить' if public else 'Включить'} создание зеркал людьми</button>")
        return (f"<div class=card id=mirrors><h2>Зеркала</h2>{table}"
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
        return ("<div class=card id=notify><h2>Уведомление зеркалам</h2>"
                "<p class=hint>Сообщение получат люди, которые запускали основной бот или зеркала "
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
        await self.app.log_action(self._uid(request), "web_reserve_add", reserve.username,
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
        await self.app.log_action(self._uid(request), "web_reserve_delete", bot_id, f"Веб-панель: удалён запасной {bot_id}")
        raise self._back("Запасной удалён.")

    async def switch_ask(self, request: web.Request) -> web.Response:
        self._need_owner(request)
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
        self._need_owner(request)
        runner = self.app.runner
        if runner is None:
            raise self._back("Переключение недоступно.")
        info = await runner.failover("вручную из панели", manual=True)
        if info is None:
            raise self._back("Переключиться не на что: живых запасных нет.")
        await self.app.log_action(self._uid(request), "web_switch", info.new_username,
                                  f"Веб-панель: основной бот переключён на @{info.new_username}")
        raise self._back(f"Основной бот теперь @{info.new_username}. Не забудьте отправить уведомление зеркалам.")

    async def reserve_check(self, request: web.Request) -> web.Response:
        out = await self.app.reserves.health_check()
        raise self._back("Проблемы: " + " | ".join(out)[:250] if out else "Все запасные в порядке.")

    async def backup_now(self, request: web.Request) -> web.Response:
        self._need_owner(request)
        from .jobs import send_backup
        text = await send_backup(self.app, "💾 Бэкап из веб-панели")
        await self.app.log_action(self._uid(request), "web_backup", "", "Веб-панель: бэкап по кнопке")
        raise self._back(re.sub(r"<[^>]+>", "", text)[:280])

    async def mirror_delete(self, request: web.Request) -> web.Response:
        data = await request.post()
        bot_id = str(data.get("bot_id", ""))
        if not bot_id.isdigit() or not await self.app.mirrors.remove(int(bot_id)):
            raise self._back("Зеркало не найдено.")
        await self.app.log_action(self._uid(request), "web_mirror_delete", bot_id, f"Веб-панель: удалено зеркало {bot_id}")
        raise self._back("Зеркало удалено.")

    async def mirror_public(self, request: web.Request) -> web.Response:
        value = 0 if self.app.store.setting("mirrors_public", 1) else 1
        await self.app.set_setting("mirrors_public", value)
        raise self._back("Создание зеркал людьми " + ("включено." if value else "выключено."))

    async def notify_ask(self, request: web.Request) -> web.Response:
        self._need_owner(request)
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
        self._need_owner(request)
        data = await request.post()
        text = str(data.get("text", "")).strip()[:3500]
        if not text or not self.app.notifier.start(text):
            raise self._back("Рассылка уже идёт или текст пустой.")
        await self.app.log_action(self._uid(request), "web_notify", "", "Веб-панель: запущено уведомление зеркалам")
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
        web.post("/reserve/check", panel.reserve_check),
        web.post("/backup/now", panel.backup_now),
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
