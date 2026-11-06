"""Админка в Telegram: зеркала и запасные токены. Токены сюда не присылаются и не показываются:
добавлять запасных безопаснее в веб-панели."""
from html import escape

from aiogram.types import Message

from ...mirrors import MirrorError
from ...ui import safe_delete
from .core import Ctx, InputError, Rows, ViewResult, action, b, back_btn, on_input, view, yes_no

STATUS = {"ready": "🟢 готов", "active": "⭐️ работает", "dead": "🔴 не рабочий"}


def _enabled(ctx: Ctx) -> bool:
    return ctx.app.mirrors is not None and ctx.app.mirrors.enabled


def _is_owner(ctx: Ctx) -> bool:
    return ctx.user_id in ctx.app.config.owner_ids


@view("mir", "mirrors")
async def view_mirrors(ctx: Ctx) -> ViewResult:
    app = ctx.app
    if not _enabled(ctx):
        return ("🪞 <b>Зеркала</b>\n\nВыключены: в .env не задан <code>SECRET_KEY</code>.",
                [back_btn("a:home")])
    listing = await app.mirrors.listing()
    alive = sum(1 for m in listing if m["running"])
    reserves = await app.reserves.listing()
    ready = sum(1 for r in reserves if r.status == "ready")
    public = bool(app.store.setting("mirrors_public", 1))
    from ...mirrors import can_create_auto
    auto = await can_create_auto(app)
    runner = app.runner
    state = "🔴 не работает, запасных нет" if runner and runner.state.down else "🟢 работает"
    html = (
        "🪞 <b>Зеркала и запасные</b>\n\n"
        f"Основной: @{escape(app.bot_username)} · {state}\n"
        f"Зеркал: <b>{alive}</b> работает из {len(listing)}\n"
        f"Запасных готово: <b>{ready}</b> из {len(reserves)}"
        + ("  ⚠️ добавьте ещё" if ready < int(app.store.setting("reserves_min", 2)) else "") + "\n"
        f"Создание зеркал людьми: <b>{yes_no(public)}</b>\n"
        + ("⚡️ Автосоздание зеркал: <b>включено</b>" if auto else
           "⚡️ Автосоздание зеркал: <b>выключено</b>. Включите Bot Management Mode у основного и запасных "
           "ботов в @BotFather, тогда у людей появится кнопка «Создать автоматически».")
    )
    rows: Rows = [
        [b("🪞 Зеркала", "a:mirl"), b("🛡 Запасные", "a:resl")],
        [b(f"🌐 Людям можно создавать: {yes_no(public)}", "x:mirpub")],
        [b(f"📣 Сообщать о новом адресе само: {yes_no(bool(app.store.setting('auto_notify', 1)))}", "x:autonot")],
        [b("🩺 Проверить запасных", "x:reschk")],
    ]
    if _is_owner(ctx) and ready:
        rows.append([b("🔀 Переключить на запасной", "a:swq", "danger")])
    rows.append(back_btn("a:home"))
    return html, rows


@view("mirl", "mirrors")
async def view_mirror_list(ctx: Ctx) -> ViewResult:
    listing = await ctx.app.mirrors.listing()
    lines = []
    rows: Rows = []
    for m in listing[:20]:
        mark = "🟢" if m["running"] else "🔴"
        lines.append(f"{mark} @{escape(m['username'])} · людей {m['users']} · владелец <code>{m['owner_id']}</code>")
        rows.append([b(f"🗑 @{m['username']}", f"a:mirdq:{m['bot_id']}")])
    html = "🪞 <b>Зеркала</b>\n\n" + ("\n".join(lines) or "Пока нет.")
    if len(listing) > 20:
        html += f"\n… и ещё {len(listing) - 20}, полный список в веб-панели"
    rows.append(back_btn("a:mir"))
    return html, rows


@view("mirdq", "mirrors")
async def view_mirror_delete_ask(ctx: Ctx, bot_id: str) -> ViewResult:
    row = await ctx.app.db.fetchone("SELECT username FROM mirrors WHERE bot_id = ?", (int(bot_id),))
    if row is None:
        return await view_mirror_list(ctx)
    return (f"Удалить зеркало <b>@{escape(row['username'])}</b>? Бот перестанет отвечать людям.",
            [[b("🗑 Да, удалить", f"x:mirdel:{bot_id}", "danger"), b("Нет", "a:mirl")]])


@action("mirdel", "mirrors")
async def act_mirror_delete(ctx: Ctx, bot_id: str):
    if await ctx.app.mirrors.remove(int(bot_id)):
        await ctx.log("mirror.delete", bot_id)
        ctx.notice = "Зеркало удалено."
    return "a:mirl"


@action("autonot", "mirrors")
async def act_auto_notify(ctx: Ctx):
    value = 0 if ctx.app.store.setting("auto_notify", 1) else 1
    await ctx.app.set_setting("auto_notify", value)
    await ctx.log("mirror.auto_notify", str(value))
    return "a:mir"


@action("mirpub", "mirrors")
async def act_mirror_public(ctx: Ctx):
    value = 0 if ctx.app.store.setting("mirrors_public", 1) else 1
    await ctx.app.set_setting("mirrors_public", value)
    await ctx.log("mirror.public", str(value))
    return "a:mir"


@view("resl", "mirrors")
async def view_reserve_list(ctx: Ctx) -> ViewResult:
    app = ctx.app
    reserves = await app.reserves.listing()
    lines = []
    for r in reserves:
        issues = app.reserves.problems.get(r.bot_id)
        extra = f"\n   ⚠️ {escape('; '.join(issues[:2]))}" if issues else ""
        lines.append(f"{r.position}. @{escape(r.username)} · {STATUS.get(r.status, r.status)}{extra}")
    html = ("🛡 <b>Запасные токены</b>\n\n" + ("\n".join(lines) or "Пока нет.") +
            "\n\nНовый запасной должен быть админом во всех каналах с правом «Приглашать пользователей».")
    return html, [[b("➕ Добавить запасной", "x:resadd", "success")], [b("🩺 Проверить", "x:reschk")],
                  back_btn("a:mir")]


@action("resadd", "mirrors")
async def act_reserve_add(ctx: Ctx):
    return await ctx.ask("resadd", "Пришлите токен запасного бота (из @BotFather). Сообщение с токеном я сразу удалю.",
                         "a:resl")


@on_input("resadd", "mirrors")
async def in_reserve_add(ctx: Ctx, message: Message):
    token = (message.text or "").strip()
    await safe_delete(ctx.app, ctx.chat_id, message.message_id)  # токен в чате не остаётся
    try:
        reserve, problems = await ctx.app.reserves.add(token)
    except MirrorError as e:
        raise InputError(str(e))
    await ctx.log("mirror.reserve_add", reserve.username)
    ctx.notice = f"✅ Запасной @{reserve.username} добавлен."
    if problems:
        ctx.notice += "\n⚠️ Сделайте его админом с правом «Приглашать пользователей»: " + "; ".join(problems[:4])
    return "a:resl"


@action("reschk", "mirrors")
async def act_reserve_check(ctx: Ctx):
    await ctx.toast("Проверяю...")
    problems = await ctx.app.reserves.health_check()
    ctx.notice = "⚠️ " + " | ".join(problems)[:300] if problems else "✅ Все запасные в порядке."
    return "a:resl"


@view("swq", "mirrors")
async def view_switch_ask(ctx: Ctx) -> ViewResult:
    if not _is_owner(ctx):
        return await view_mirrors(ctx)
    return ("🔀 <b>Переключить основной бот на запасной?</b>\n\nЛюди старого бота останутся без него, пока не "
            "получат уведомление.", [[b("Да, переключить", "x:swgo", "danger"), b("Нет", "a:mir")]])


@action("swgo", "mirrors")
async def act_switch(ctx: Ctx):
    if not _is_owner(ctx) or ctx.app.runner is None:
        return "a:mir"
    info = await ctx.app.runner.failover("вручную из админки", manual=True)
    ctx.notice = (f"✅ Основной бот теперь @{info.new_username}." if info else "Запасных нет.")
    if info:
        await ctx.log("mirror.switch", info.new_username)
    return "a:mir"
