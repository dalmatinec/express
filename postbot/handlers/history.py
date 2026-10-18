"""История постов: список, карточка поста и что с ним можно сделать."""
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..app import App
from ..services import posts as P
from ..services.media import KIND_NAMES
from ..services.publisher import DM_TITLE, cancel_scheduled, delete_everywhere, results_text
from ..states import Hist
from ..utils.text import short
from .common import clicked, home, parts

router = Router(name="history")
router.message.filter(F.chat.type == "private")

PAGE = 8


async def show_list(app: App, uid: int, page: int, edit: Message | None = None, note: str = "") -> None:
    posts = await P.list_posts(app)
    chunk, page, pages = ui.paginate(posts, page, PAGE)
    app.screens.setdefault(uid, {})["hist_page"] = page
    rows = [[ui.b(f"#{p.id} · {short(p.name, 30)}", f"h:{p.id}:card")] for p in chunk]
    rows.append(ui.nav_row(page, pages, lambda x: f"h:list:{x}"))
    scheduled = await app.db.fetchval("SELECT COUNT(*) FROM publications WHERE status = 'scheduled'")
    if scheduled:
        rows.append([ui.b(f"⏰ Отложенные ({scheduled})", "h:0:sched")])
    text = f"🗂 История постов ({len(posts)}):" if posts else "🗂 История пуста.\n\nСоздайте первый пост."
    if note:
        text = f"{note}\n\n{text}"
    await ui.show(app, uid, text, ui.kb(rows), edit=edit)


async def show_scheduled(app: App, uid: int, edit: Message | None = None) -> None:
    rows_db = await app.db.fetchall(
        "SELECT pub.post_id, pub.run_at, p.title, c.title AS channel FROM publications pub "
        "JOIN posts p ON p.id = pub.post_id LEFT JOIN channels c ON c.id = pub.channel_id "
        "WHERE pub.status = 'scheduled' ORDER BY pub.run_at LIMIT 40")
    rows = [[ui.b(f"{app.fmt(r['run_at'], short=True)} · #{r['post_id']} {short(r['title'] or '', 18)} · "
                  f"{short(r['channel'] or 'Личка', 14)}", f"h:{r['post_id']}:card")] for r in rows_db]
    rows.append([ui.b("🔙 К истории", "h:list:0")])
    text = "⏰ Отложенные публикации:" if rows_db else "⏰ Отложенных публикаций нет."
    await ui.show(app, uid, text, ui.kb(rows), edit=edit)


async def card_view(app: App, post_id: int) -> tuple[str, list] | None:
    post = await P.get_post(app, post_id)
    if post is None:
        return None
    buttons = await P.get_buttons(app, post_id)
    media = await app.media.get(post.media_id)
    pubs = await P.publications(app, post_id)
    channels = {c.id: c for c in await app.all_channels()}

    lines = [f"🏷 {escape(post.name)}", f"Пост #{post.id} от {app.fmt(post.created_at)}"]
    if post.updated_at - post.created_at > 60:
        lines.append(f"Изменён {app.fmt(post.updated_at)}")
    lines.append(f"🖼 Медиа: {KIND_NAMES[media.kind] if media else 'нет'} · 🔘 Кнопок: {len(buttons)}")

    sent = [x for x in pubs if x.status == "sent"]
    sched = [x for x in pubs if x.status == "scheduled"]
    failed = [x for x in pubs if x.status == "failed"][-3:]
    def where(x) -> str:
        if x.is_dm:
            return DM_TITLE
        c = channels.get(x.channel_id)
        return escape(c.title) if c else str(x.channel_id)

    if sent:
        lines.append("\n📤 Сейчас висит:")
        for x in sent:
            c = channels.get(x.channel_id)
            title = where(x)
            link = c.post_link(x.message_id or 0) if c else None
            name = f'<a href="{escape(link)}">{title}</a>' if link else title
            tail = f", удалится {app.fmt(x.delete_at, short=True)}" if x.delete_at else ""
            lines.append(f"• {name} с {app.fmt(x.sent_at, short=True)}{tail}")
    if sched:
        lines.append("\n⏰ Отложено:")
        for x in sched:
            lines.append(f"• {where(x)} на {app.fmt(x.run_at, short=True)}")
    if failed:
        lines.append("\n❌ Не вышло:")
        for x in failed:
            lines.append(f"• {where(x)}: {escape(x.error or '')}")
    if not pubs:
        lines.append("\nЕщё нигде не публиковался.")

    pid = post.id
    rows = [
        [ui.b("👁 Показать пост", f"h:{pid}:show")],
        [ui.b("✏️ Изменить: текст, медиа, кнопки", f"h:{pid}:edit", "primary")],
        [ui.b("🔁 Опубликовать повторно", f"h:{pid}:re", "success")],
        [ui.b("🏷 Переименовать", f"h:{pid}:ren"), ui.b("📑 Копия", f"h:{pid}:copy")],
    ]
    if sent:
        rows.append([ui.b(f"🧹 Удалить везде, где вышел ({len(sent)})", f"h:{pid}:dch")])
    if sched:
        rows.append([ui.b(f"🚫 Отменить отложенные ({len(sched)})", f"h:{pid}:dsch")])
    rows.append([ui.b("🗑 Удалить из истории", f"h:{pid}:del", "danger")])
    rows.append([ui.b("🔙 К истории", "h:list:back")])
    return "\n".join(lines), rows


async def show_card(app: App, uid: int, post_id: int, edit: Message | None = None, note: str = "",
                    with_post: bool = False) -> None:
    view = await card_view(app, post_id)
    if view is None:
        await show_list(app, uid, 0, edit=edit, note="Этот пост уже удалён.")
        return
    text, rows = view
    if note:
        text = f"{note}\n\n{text}"
    if with_post:
        from .editor import render
        await render(app, uid, post_id, text, ui.kb(rows), edit=edit, refresh=True)
    else:
        await ui.show(app, uid, text, ui.kb(rows), edit=edit)


@router.callback_query(F.data.startswith("h:"))
async def history_cb(cq: CallbackQuery, state: FSMContext, app: App) -> None:
    p = parts(cq)
    uid, msg = cq.from_user.id, clicked(cq)
    await state.clear()
    if p[1] == "list":
        await cq.answer()
        page = app.screens.get(uid, {}).get("hist_page", 0) if p[2] == "back" else int(p[2])
        await show_list(app, uid, page, edit=msg)
        return
    post_id, action = int(p[1]), p[2]
    if action == "sched":
        await cq.answer()
        await show_scheduled(app, uid, edit=msg)
        return
    post = await P.get_post(app, post_id)
    if post is None:
        await cq.answer("Пост уже удалён", show_alert=True)
        await show_list(app, uid, 0, edit=msg)
        return
    if post.is_draft:
        from .editor import open_editor
        await cq.answer()
        await open_editor(app, uid, post_id, edit=msg)
        return

    if action == "card":
        await cq.answer()
        await show_card(app, uid, post_id, edit=msg)

    elif action == "show":
        await cq.answer()
        await show_card(app, uid, post_id, edit=msg, with_post=True)

    elif action == "re":
        await cq.answer()
        from .publish import show_channels
        await show_channels(app, uid, post_id, state, edit=msg)

    elif action == "edit":
        await cq.answer()
        from .editor import open_editor
        await open_editor(app, uid, post_id, edit=msg)

    elif action == "copy":
        await cq.answer()
        new_id = await P.clone_post(app, post_id, uid, as_edit=False)
        from .editor import open_editor
        await open_editor(app, uid, new_id, edit=msg, note=f"📑 Это копия поста #{post_id}, новый пост.")

    elif action == "ren":
        await cq.answer()
        await state.set_state(Hist.rename)
        await state.update_data(post_id=post_id)
        await ui.show(app, uid, f"🏷 Сейчас: {escape(post.name)}\n\nПришлите новое название.",
                      ui.kb([[ui.b("🔙 Назад", f"h:{post_id}:card")]]), edit=msg)

    elif action == "dch":
        await cq.answer()
        await ui.show(app, uid, "Удалить этот пост везде, где он сейчас висит? В истории он останется.",
                      ui.kb([[ui.b("🧹 Да, удалить из каналов", f"h:{post_id}:dchy", "danger")],
                             [ui.b("🔙 Нет", f"h:{post_id}:card")]]), edit=msg)

    elif action == "dchy":
        await cq.answer("Удаляю...")
        results = await delete_everywhere(app, post_id)
        await show_card(app, uid, post_id, edit=msg, note="🧹 " + results_text(results, "Удалено"))

    elif action == "dsch":
        count = await cancel_scheduled(app, post_id)
        await cq.answer(f"Отменено: {count}")
        await show_card(app, uid, post_id, edit=msg)

    elif action == "del":
        await cq.answer()
        await ui.show(app, uid,
                      f"Удалить пост #{post_id} · {escape(post.name)} из истории?\n\n"
                      "Из каналов он не пропадёт. Но отложенные публикации отменятся, автоудаление не сработает, "
                      "а всплывающие окна на его кнопках в каналах перестанут отвечать.",
                      ui.kb([[ui.b("🗑 Да, удалить из истории", f"h:{post_id}:dely", "danger")],
                             [ui.b("🔙 Нет", f"h:{post_id}:card")]]), edit=msg)

    elif action == "dely":
        await P.delete_post(app, post_id)
        await app.media.cleanup()
        await cq.answer("Удалено")
        page = app.screens.get(uid, {}).get("hist_page", 0)
        await show_list(app, uid, page, edit=msg, note="🗑 Пост удалён из истории.")
    else:
        await cq.answer()


@router.message(Hist.rename)
async def got_rename(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    post_id = data.get("post_id", 0)
    await ui.drop_input(message)
    title = short(message.text or "", 60)
    if not title:
        await ui.show(app, uid, "⚠️ Нужно название текстом. Пришлите ещё раз.",
                      ui.kb([[ui.b("🔙 Назад", f"h:{post_id}:card")]]))
        return
    if await P.get_post(app, post_id) is None:
        await state.clear()
        await home(app, uid)
        return
    await P.update_post(app, post_id, title=title)
    await state.clear()
    await show_card(app, uid, post_id, note="🏷 Переименовано.")
