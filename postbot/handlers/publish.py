"""Публикация: выбор каналов, сразу или по времени."""
from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..app import App, now
from ..services import posts as P
from ..services.publisher import publish, results_text, schedule
from ..states import Pub
from ..utils.when import parse_when
from .common import clicked, home, parts

router = Router(name="publish")
router.message.filter(F.chat.type == "private")


def _back(post_is_draft: bool, post_id: int) -> str:
    return f"e:{post_id}:v" if post_is_draft else f"h:{post_id}:card"


async def show_channels(app: App, uid: int, post_id: int, state: FSMContext, edit: Message | None = None,
                        note: str = "") -> None:
    post = await P.get_post(app, post_id)
    if post is None:
        await home(app, uid, note="Пост не найден.")
        return
    keep = app.screens.get(uid, {}).get("preview_post") == post_id
    channels = await app.channels_for(uid)
    back = _back(bool(post.is_draft), post_id)
    if not channels:
        rows = []
        if app.can_manage_channels(uid):
            rows.append([ui.b("➕ Подключить канал", "a:chadd", "success")])
        rows.append([ui.b("🔙 Назад", back)])
        await ui.show(app, uid, "📢 Каналов пока нет.\n\nДобавьте бота админом в канал с правом публиковать "
                                "сообщения, он подключится сам.", ui.kb(rows), edit=edit, keep_preview=keep)
        return

    data = await state.get_data()
    ok_ids = [c.id for c in channels if c.ok]
    if data.get("pub_post") != post_id:
        sel = ok_ids[:1] if len(ok_ids) == 1 else []
        await state.update_data(pub_post=post_id, sel=sel)
    else:
        sel = [cid for cid in data.get("sel", []) if cid in ok_ids]

    rows = []
    for c in channels:
        if c.ok:
            rows.append([ui.b(f"{ui.mark(c.id in sel)} {c.title}", f"p:{post_id}:t:{c.id}")])
        else:
            rows.append([ui.b(f"⚠️ {c.title}", f"p:{post_id}:bad:{c.id}")])
    if len(ok_ids) > 1:
        all_on = set(ok_ids) <= set(sel)
        rows.append([ui.b("Снять все" if all_on else "Выбрать все", f"p:{post_id}:all")])
    rows.append([ui.b("🚀 Опубликовать сейчас", f"p:{post_id}:now", "success"),
                 ui.b("⏰ По времени", f"p:{post_id}:time")])
    rows.append([ui.b("🔙 Назад", back)])
    text = (f"📤 Куда публикуем пост #{post.source_id or post.id} · {escape(post.name)}?\n\n"
            "Отметьте каналы и нажмите Опубликовать.")
    if any(not c.ok for c in channels):
        text += "\n⚠️ - там у бота нет права публиковать, нажмите, чтобы узнать подробнее."
    if note:
        text = f"{note}\n\n{text}"
    await ui.show(app, uid, text, ui.kb(rows), edit=edit, keep_preview=keep)


async def _finish(app: App, uid: int, post_id: int) -> int:
    """Если это черновик, переносим его в историю. -> id поста в истории."""
    final_id = await P.commit_draft(app, post_id)
    await app.media.cleanup()
    return final_id


async def _selected(app: App, uid: int, state: FSMContext) -> list[int]:
    data = await state.get_data()
    allowed = {c.id for c in await app.channels_for(uid) if c.ok}
    return [cid for cid in data.get("sel", []) if cid in allowed]


def time_prompt(app: App) -> str:
    return (f"⏰ Когда опубликовать? Время по поясу {app.config.tz_name}, сейчас {app.fmt(now(), short=True)}.\n\n"
            "Напишите, например:\n<code>18:30</code>\n<code>25.10 12:00</code>\n<code>завтра 10:00</code>\n"
            "<code>+2ч</code> или <code>+30м</code>\n\nИли выберите ниже.")


async def _schedule(app: App, uid: int, post_id: int, state: FSMContext, when: datetime) -> None:
    sel = await _selected(app, uid, state)
    if not sel:
        await show_channels(app, uid, post_id, state, note="⚠️ Отметьте хотя бы один канал.")
        return
    final_id = await _finish(app, uid, post_id)
    await schedule(app, final_id, sel, int(when.timestamp()), uid)
    await state.clear()
    names = ", ".join(escape(c.title) for c in await app.all_channels() if c.id in sel)
    from . import history
    await history.show_card(app, uid, final_id,
                            note=f"⏰ Запланировано на {app.fmt(int(when.timestamp()))}: {names}")


@router.callback_query(F.data.startswith("p:"))
async def publish_cb(cq: CallbackQuery, state: FSMContext, app: App) -> None:
    p = parts(cq)
    post_id, action = int(p[1]), p[2]
    uid, msg = cq.from_user.id, clicked(cq)
    post = await P.get_post(app, post_id)
    if post is None:
        await cq.answer("Пост не найден", show_alert=True)
        return
    await state.set_state(None)

    if action == "ch":
        await cq.answer()
        await show_channels(app, uid, post_id, state, edit=msg)

    elif action == "t":
        cid = int(p[3])
        data = await state.get_data()
        sel = list(data.get("sel", []))
        if cid in sel:
            sel.remove(cid)
        else:
            sel.append(cid)
        await state.update_data(pub_post=post_id, sel=sel)
        await cq.answer()
        await show_channels(app, uid, post_id, state, edit=msg)

    elif action == "all":
        ok_ids = [c.id for c in await app.channels_for(uid) if c.ok]
        data = await state.get_data()
        sel = [] if set(ok_ids) <= set(data.get("sel", [])) else ok_ids
        await state.update_data(pub_post=post_id, sel=sel)
        await cq.answer()
        await show_channels(app, uid, post_id, state, edit=msg)

    elif action == "bad":
        channel = await app.channel(int(p[3]))
        if channel is None or not channel.is_present:
            why = "Бота нет в этом чате. Добавьте его туда админом."
        else:
            why = "У бота нет права публиковать сообщения. Дайте его в настройках админов канала."
        await cq.answer(why, show_alert=True)

    elif action == "now":
        sel = await _selected(app, uid, state)
        if not sel:
            await cq.answer("Отметьте хотя бы один канал", show_alert=True)
            return
        await cq.answer("Публикую...")
        final_id = await _finish(app, uid, post_id)
        results = await publish(app, final_id, sel, uid)
        await state.clear()
        from . import history
        await history.show_card(app, uid, final_id, note="📤 " + results_text(results), edit=msg)

    elif action == "time":
        if not await _selected(app, uid, state):
            await cq.answer("Сначала отметьте каналы", show_alert=True)
            return
        await cq.answer()
        await state.set_state(Pub.time)
        rows = [[ui.b("+1 ч", f"p:{post_id}:at:60"), ui.b("+3 ч", f"p:{post_id}:at:180"),
                 ui.b("+6 ч", f"p:{post_id}:at:360")],
                [ui.b("Завтра 09:00", f"p:{post_id}:at:t9"), ui.b("Завтра 12:00", f"p:{post_id}:at:t12"),
                 ui.b("Завтра 18:00", f"p:{post_id}:at:t18")],
                [ui.b("🔙 Назад", f"p:{post_id}:ch")]]
        keep = app.screens.get(uid, {}).get("preview_post") == post_id
        await ui.show(app, uid, time_prompt(app), ui.kb(rows), edit=msg, keep_preview=keep)

    elif action == "at":
        arg = p[3]
        local_now = datetime.now(app.config.tz)
        if arg.startswith("t"):
            tomorrow = local_now + timedelta(days=1)
            when = tomorrow.replace(hour=int(arg[1:]), minute=0, second=0, microsecond=0)
        else:
            when = local_now + timedelta(minutes=int(arg))
        await cq.answer()
        await _schedule(app, uid, post_id, state, when)
    else:
        await cq.answer()


@router.message(Pub.time)
async def got_time(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    post_id = data.get("pub_post", 0)
    await ui.drop_input(message)
    when = parse_when(message.text or "", app.config.tz)
    back = ui.kb([[ui.b("🔙 Назад", f"p:{post_id}:ch")]])
    keep = app.screens.get(uid, {}).get("preview_post") == post_id
    if when is None:
        await ui.show(app, uid, "⚠️ Не понял время.\n\n" + time_prompt(app), back, keep_preview=keep)
        return
    if when.timestamp() < now() + 30:
        await ui.show(app, uid, "⚠️ Это время уже прошло.\n\n" + time_prompt(app), back, keep_preview=keep)
        return
    await _schedule(app, uid, post_id, state, when)
