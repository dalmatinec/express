"""Конструктор поста: текст, медиа, название, настройки. Сверху всегда предпросмотр поста, под ним панель."""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from .. import ui
from ..app import App
from ..models import Post
from ..services import posts as P
from ..services.media import KIND_NAMES, media_of
from ..services.publisher import results_text, update_everywhere
from ..services.render import RenderError, send_post
from ..states import Edit
from ..utils.text import CAPTION_LIMIT, LABEL_LIMIT, TEXT_LIMIT, auto_title, h, html_to_plain, message_html, short
from .common import clicked, home, parts

log = logging.getLogger(__name__)
router = Router(name="editor")
router.message.filter(F.chat.type == "private")

AUTODELETE = [0, 60, 180, 360, 720, 1440, 2880, 4320, 10080]
PREVIEW_MODES = ["off", "auto", "small", "large"]
PREVIEW_NAMES = {"off": "выкл", "auto": "как решит Telegram", "small": "маленькое", "large": "большое"}

CONTENT_HINT = (
    "Пришлите текст поста. Можно сразу с фото, видео, GIF или файлом, тогда текст станет подписью.\n"
    "Жирный, курсив, ссылки, спойлеры, цитаты и премиум-эмодзи сохранятся как есть.\n\n"
    "Можно просто переслать готовый пост из канала, кнопки со ссылками подтянутся сами."
)


def minutes_text(minutes: int) -> str:
    if minutes <= 0:
        return "выкл"
    if minutes % 1440 == 0:
        return f"через {minutes // 1440} дн"
    return f"через {minutes // 60} ч" if minutes % 60 == 0 else f"через {minutes} мин"


def settings_line(post: Post, has_media: bool) -> str:
    items = []
    if post.silent:
        items.append("без звука")
    if post.protect:
        items.append("запрет копирования")
    if has_media and post.spoiler:
        items.append("спойлер")
    if has_media and post.caption_above:
        items.append("текст над медиа")
    if not has_media and post.preview != "off":
        items.append(f"превью ссылки: {PREVIEW_NAMES[post.preview]}")
    if post.pin:
        items.append("закрепить")
    if post.delete_after:
        items.append(f"удалить {minutes_text(post.delete_after)}")
    return ", ".join(items) if items else "обычные"


def check_length(html: str, has_media: bool) -> str | None:
    size = len(html_to_plain(html))
    limit = CAPTION_LIMIT if has_media else TEXT_LIMIT
    if size > limit:
        what = "подпись к медиа" if has_media else "текст"
        return f"⚠️ Слишком длинный {what}: {size} символов, Telegram пропускает до {limit}."
    return None


# ---------- показ ----------

async def render(app: App, user_id: int, post_id: int, text: str, markup: InlineKeyboardMarkup | None, *,
                 edit: Message | None = None, refresh: bool = False) -> None:
    """Панель под предпросмотром. refresh=True - пост поменялся, отправляем предпросмотр заново."""
    scr = app.screens.setdefault(user_id, {})
    if not refresh and scr.get("preview_post") == post_id and scr.get("preview"):
        await ui.show(app, user_id, text, markup, edit=edit, keep_preview=True)
        return

    post = await P.get_post(app, post_id)
    buttons = await P.get_buttons(app, post_id)
    old = (scr.get("preview"), scr.get("panel"))
    note = ""
    preview_id = None
    if post is not None:
        try:
            sent = await send_post(app, user_id, post, buttons, preview=True)
            preview_id = sent.message_id
        except (TelegramAPIError, RenderError) as e:
            note = f"\n\n⚠️ Предпросмотр не отправился: {h(getattr(e, 'message', str(e)))}"
    msg = await app.bot.send_message(user_id, text + note, reply_markup=markup)
    scr.update(panel=msg.message_id, preview=preview_id, preview_post=post_id)
    for mid in old:
        await ui.retire(app, user_id, mid)
    if edit is not None and edit.message_id not in old and edit.message_id not in (msg.message_id, preview_id):
        await ui.retire(app, user_id, edit.message_id)


async def open_editor(app: App, user_id: int, post_id: int, *, edit: Message | None = None,
                      refresh: bool = True, note: str = "") -> None:
    post = await P.get_post(app, post_id)
    if post is None:
        await home(app, user_id, note="Этот пост уже удалён.")
        return
    buttons = await P.get_buttons(app, post_id)
    media = await app.media.get(post.media_id)
    saved = not post.is_draft
    editing = post.source_id is not None
    if saved:
        head = f"✏️ Пост #{post.id} · {h(post.name)}\nМеняйте что нужно, всё сохраняется сразу."
    elif editing:
        head = f"✏️ Правка поста #{post.source_id}"
    else:
        head = f"📝 Новый пост #{post.id}"
    text = (f"{head}\n\n"
            f"🏷 Название: {h(post.title) or 'не задано'}\n"
            f"🖼 Медиа: {KIND_NAMES[media.kind] if media else 'нет'}\n"
            f"🔘 Кнопок: {len(buttons)}\n"
            f"⚙️ Настройки: {settings_line(post, media is not None)}\n\n"
            "Выше предпросмотр, так пост будет выглядеть в канале.")
    if note:
        text = f"{note}\n\n{text}"
    pid = post.id
    rows = [
        [ui.b("✏️ Текст", f"e:{pid}:text"), ui.b("🖼 Медиа", f"e:{pid}:media")],
        [ui.b(f"🔘 Кнопки ({len(buttons)})", f"k:{pid}:list"), ui.b("⚙️ Настройки", f"e:{pid}:set")],
        [ui.b("🏷 Название", f"e:{pid}:title")],
        [ui.b("📤 Опубликовать", f"e:{pid}:pub", "success")],
    ]
    if saved:
        live = await P.publications(app, pid, "sent")
        if live:
            rows.append([ui.b(f"🔄 Обновить там, где он уже вышел ({len(live)})", f"e:{pid}:upd", "primary")])
        rows.append([ui.b("🔙 К посту", f"h:{pid}:card")])
        await render(app, user_id, pid, text, ui.kb(rows), edit=edit, refresh=refresh)
        return
    if editing:
        live = await P.publications(app, post.source_id, "sent")
        if live:
            rows.append([ui.b(f"🔄 Сохранить и обновить в каналах ({len(live)})", f"e:{pid}:upd", "primary")])
        rows.append([ui.b("💾 Сохранить без публикации", f"e:{pid}:save")])
    else:
        rows.append([ui.b("💾 Сохранить в историю", f"e:{pid}:save")])
    rows.append([ui.b("❌ Отмена", f"e:{pid}:cx", "danger")])
    await render(app, user_id, pid, text, ui.kb(rows), edit=edit, refresh=refresh)


async def start_new(app: App, user_id: int, state: FSMContext, edit: Message | None = None,
                    force: bool = False) -> None:
    await state.clear()
    draft = await P.find_new_draft(app, user_id)
    if draft and not force:
        await ui.show(app, user_id,
                      f"У вас есть незаконченный пост #{draft.id}: {h(draft.title or auto_title(draft.html))}\n\n"
                      "Продолжить его или начать новый?",
                      ui.kb([[ui.b("▶️ Продолжить", f"e:{draft.id}:v", "primary")],
                             [ui.b("🆕 Начать новый", "e:0:new")],
                             [ui.b("🔙 Меню", "m:home")]]), edit=edit)
        return
    await state.set_state(Edit.content)
    await ui.show(app, user_id, f"📝 Новый пост\n\n{CONTENT_HINT}", ui.cancel_kb("m:home"), edit=edit)


def _imported_buttons(message: Message) -> list[list[dict]]:
    """Кнопки пересланного поста. Кнопки бота (callback) перенести нельзя, только ссылки и копирование."""
    rows: list[list[dict]] = []
    if not message.reply_markup or not getattr(message.reply_markup, "inline_keyboard", None):
        return rows
    for row in message.reply_markup.inline_keyboard:
        out = []
        for btn in row:
            base = dict(label=btn.text[:LABEL_LIMIT], icon=btn.icon_custom_emoji_id, style=btn.style)
            if btn.url:
                out.append(dict(base, kind="url", value=btn.url))
            elif btn.copy_text:
                out.append(dict(base, kind="copy", value=btn.copy_text.text))
        if out:
            rows.append(out)
    return rows


async def _ask(app: App, user_id: int, text: str, back: str, extra: list | None = None) -> None:
    rows = [extra] if extra else []
    rows.append([ui.b("🔙 Назад", back)])
    await ui.show(app, user_id, text, ui.kb(rows), keep_preview=True)


# ---------- новый пост: первый шаг ----------

@router.message(Edit.content)
async def got_content(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    await ui.drop_input(message)
    media = media_of(message)
    html = message_html(message)
    if media is None and not html:
        await ui.show(app, uid, "⚠️ Такое сообщение не подходит.\n\n" + CONTENT_HINT, ui.cancel_kb("m:home"))
        return
    error = check_length(html, media is not None)
    if error:
        await ui.show(app, uid, f"{error}\n\n{CONTENT_HINT}", ui.cancel_kb("m:home"))
        return
    note = ""
    media_id = None
    if media is not None:
        media_id, saved = await app.media.save(app.bot, *media)
        if not saved:
            note = "⚠️ Файл больше 20 МБ, бот не может скачать его себе. Пост работает, но при переезде " \
                   "на новый токен это медиа придётся загрузить заново."
    post_id = await P.create_draft(app, uid, html, media_id)
    imported = await P.import_buttons(app, post_id, _imported_buttons(message))
    if imported:
        note = (note + "\n" if note else "") + f"🔘 Перенёс кнопок из пересланного поста: {imported}"
    if message.media_group_id:
        note = (note + "\n" if note else "") + "⚠️ Альбомы не поддерживаются, взял только первое медиа."
    await state.clear()
    await open_editor(app, uid, post_id, note=note)


@router.callback_query(F.data.startswith("e:"))
async def editor_cb(cq: CallbackQuery, state: FSMContext, app: App) -> None:
    p = parts(cq)
    post_id, action = int(p[1]), p[2]
    uid, msg = cq.from_user.id, clicked(cq)

    if action == "new":
        old = await P.find_new_draft(app, uid)
        while old is not None:
            await P.drop_draft(app, old.id)
            old = await P.find_new_draft(app, uid)
        await cq.answer()
        await start_new(app, uid, state, edit=msg, force=True)
        return

    post = await P.get_post(app, post_id)
    if post is None:
        await cq.answer("Пост не найден, возможно его уже удалили", show_alert=True)
        await home(app, uid)
        return
    await state.clear()
    media = await app.media.get(post.media_id)

    if action == "v":
        await cq.answer()
        refresh = app.screens.get(uid, {}).get("preview_post") != post_id
        await open_editor(app, uid, post_id, edit=msg, refresh=refresh)

    elif action == "text":
        await cq.answer()
        await state.set_state(Edit.text)
        await state.update_data(post_id=post_id)
        limit = CAPTION_LIMIT if media else TEXT_LIMIT
        await _ask(app, uid, f"✏️ Пришлите новый текст поста, до {limit} символов.\n"
                             "Форматирование и премиум-эмодзи сохранятся.", f"e:{post_id}:v")

    elif action == "media":
        await cq.answer()
        await state.set_state(Edit.media)
        await state.update_data(post_id=post_id)
        extra = [ui.b("🗑 Убрать медиа", f"e:{post_id}:mdel", "danger")] if media else None
        now_text = f"Сейчас: {KIND_NAMES[media.kind]}." if media else "Сейчас медиа нет."
        await _ask(app, uid, f"🖼 {now_text}\n\nПришлите фото, видео, GIF или файл.", f"e:{post_id}:v", extra)

    elif action == "mdel":
        await P.update_post(app, post_id, media_id=None, spoiler=0, caption_above=0)
        await cq.answer("Медиа убрано")
        await open_editor(app, uid, post_id)

    elif action == "title":
        await cq.answer()
        await _ask_title(app, uid, state, post, "")

    elif action == "notitle":
        await cq.answer()
        await P.update_post(app, post_id, title=auto_title(post.html))
        await _after_title(app, uid, state, post_id, p[3] if len(p) > 3 else "", msg)

    elif action == "set":
        await cq.answer()
        await show_settings(app, uid, post_id, edit=msg)

    elif action == "tg":
        await toggle_setting(app, cq, post, p[3], media is not None)

    elif action in ("pub", "save", "upd"):
        await cq.answer()
        if not post.title:
            await _ask_title(app, uid, state, post, action)
            return
        await _after_title(app, uid, state, post_id, action, msg)

    elif action == "cx" and not post.is_draft:
        await cq.answer()
        from . import history
        await history.show_card(app, uid, post_id, edit=msg)

    elif action == "cx":
        await cq.answer()
        what = "Изменения не сохранятся." if post.source_id else "Пост удалится."
        await ui.show(app, uid, f"Выйти без сохранения? {what}",
                      ui.kb([[ui.b("🗑 Да, выйти", f"e:{post_id}:cxy", "danger")],
                             [ui.b("🔙 Нет, вернуться", f"e:{post_id}:v")]]), edit=msg, keep_preview=True)

    elif action == "cxy" and post.is_draft:
        await cq.answer("Отменено")
        await P.drop_draft(app, post_id)
        await app.media.cleanup()
        if post.source_id:
            from . import history
            await history.show_card(app, uid, post.source_id)
        else:
            await home(app, uid, note="Пост отменён.")
    else:
        await cq.answer()


async def _ask_title(app: App, uid: int, state: FSMContext, post: Post, after: str) -> None:
    await state.set_state(Edit.title)
    await state.update_data(post_id=post.id, after=after)
    now_title = f"Сейчас: {h(post.title)}\n\n" if post.title else ""
    hint = auto_title(post.html)
    await _ask(app, uid, f"🏷 {now_title}Пришлите название поста. Оно видно только в истории, чтобы было проще "
                         f"разобраться, где какой пост.\n\nЕсли не нужно, назову по первой строке: {h(hint)}",
               f"e:{post.id}:v", [ui.b("⏭ Назвать по первой строке", f"e:{post.id}:notitle:{after}")])


async def _after_title(app: App, uid: int, state: FSMContext, post_id: int, after: str,
                       edit: Message | None) -> None:
    await state.clear()
    if after == "pub":
        from . import publish
        await publish.show_channels(app, uid, post_id, state, edit=edit)
        return
    if after in ("save", "upd"):
        draft = await P.get_post(app, post_id)
        old_media = None
        if draft and draft.source_id:
            source = await P.get_post(app, draft.source_id)
            old_media = source.media_id is not None if source else None
        final_id = await P.commit_draft(app, post_id)
        await app.media.cleanup()
        note = "💾 Сохранено в историю."
        if after == "upd":
            results = await update_everywhere(app, final_id, old_media)
            note = "🔄 " + results_text(results, "Обновлено")
        from . import history
        await history.show_card(app, uid, final_id, note=note)
        return
    await open_editor(app, uid, post_id, edit=edit, refresh=False)


# ---------- ввод ----------

@router.message(Edit.text)
async def got_text(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    post_id = data.get("post_id", 0)
    await ui.drop_input(message)
    post = await P.get_post(app, post_id)
    if post is None:
        await state.clear()
        await home(app, uid)
        return
    html = message_html(message)
    if not html:
        await _ask(app, uid, "⚠️ Нужен текст. Пришлите текст поста.", f"e:{post_id}:v")
        return
    error = check_length(html, post.media_id is not None)
    if error:
        await _ask(app, uid, f"{error}\nСократите и пришлите ещё раз.", f"e:{post_id}:v")
        return
    await P.update_post(app, post_id, html=html)
    await state.clear()
    await open_editor(app, uid, post_id, note="✏️ Текст обновлён.")


@router.message(Edit.media)
async def got_media(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    post_id = data.get("post_id", 0)
    await ui.drop_input(message)
    post = await P.get_post(app, post_id)
    if post is None:
        await state.clear()
        await home(app, uid)
        return
    media = media_of(message)
    if media is None:
        await _ask(app, uid, "⚠️ Это не медиа. Пришлите фото, видео, GIF или файл.", f"e:{post_id}:v")
        return
    html = post.html or message_html(message)
    error = check_length(html, True)
    if error:
        await _ask(app, uid, f"{error}\nС медиа текст должен быть короче. Сначала сократите текст поста.",
                   f"e:{post_id}:v")
        return
    media_id, saved = await app.media.save(app.bot, *media)
    await P.update_post(app, post_id, media_id=media_id, html=html)
    await state.clear()
    await app.media.cleanup()
    note = "🖼 Медиа обновлено."
    if not saved:
        note += "\n⚠️ Файл больше 20 МБ, в бэкап он не попадёт."
    await open_editor(app, uid, post_id, note=note)


@router.message(Edit.title)
async def got_title(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    post_id, after = data.get("post_id", 0), data.get("after", "")
    await ui.drop_input(message)
    title = short(message.text or "", 60)
    if not title:
        await _ask(app, uid, "⚠️ Нужно название текстом.", f"e:{post_id}:v")
        return
    await P.update_post(app, post_id, title=title)
    await _after_title(app, uid, state, post_id, after, None)


# ---------- настройки ----------

async def show_settings(app: App, uid: int, post_id: int, edit: Message | None = None,
                        refresh: bool = False) -> None:
    post = await P.get_post(app, post_id)
    if post is None:
        return
    media = await app.media.get(post.media_id)
    extras = media is not None and media.kind in ("photo", "video", "animation")
    pid = post.id
    rows = [
        [ui.b(f"{ui.mark(post.silent)} 🔕 Без звука", f"e:{pid}:tg:silent")],
        [ui.b(f"{ui.mark(post.protect)} 🔒 Запрет пересылки и копирования", f"e:{pid}:tg:protect")],
    ]
    if extras:
        rows.append([ui.b(f"{ui.mark(post.spoiler)} 🫣 Медиа под спойлером", f"e:{pid}:tg:spoiler")])
        rows.append([ui.b(f"{ui.mark(post.caption_above)} ⬆️ Текст над медиа", f"e:{pid}:tg:above")])
    if media is None:
        rows.append([ui.b(f"🔗 Превью ссылки: {PREVIEW_NAMES[post.preview]}", f"e:{pid}:tg:preview")])
        if post.preview != "off":
            rows.append([ui.b(f"{ui.mark(post.preview_above)} ⬆️ Превью над текстом", f"e:{pid}:tg:pabove")])
    rows.append([ui.b(f"{ui.mark(post.pin)} 📌 Закрепить после публикации", f"e:{pid}:tg:pin")])
    rows.append([ui.b(f"🗑 Автоудаление: {minutes_text(post.delete_after)}", f"e:{pid}:tg:autodel")])
    rows.append([ui.b("🔙 К посту", f"e:{pid}:v")])
    text = ("⚙️ Настройки поста\n\n"
            "Нажмите на пункт, чтобы включить или выключить. Превью ссылки и автоудаление переключаются по кругу.\n"
            "Автоудаление: бот сам удалит пост из канала через выбранное время.")
    await render(app, uid, pid, text, ui.kb(rows), edit=edit, refresh=refresh)


async def toggle_setting(app: App, cq: CallbackQuery, post: Post, key: str, has_media: bool) -> None:
    values: dict = {}
    look_changes = False  # меняется ли вид поста, тогда переотправляем предпросмотр
    if key in ("silent", "protect", "pin", "spoiler"):
        values[key] = 0 if getattr(post, key) else 1
        look_changes = key == "spoiler"
    elif key == "above":
        values["caption_above"] = 0 if post.caption_above else 1
        look_changes = True
    elif key == "pabove":
        values["preview_above"] = 0 if post.preview_above else 1
        look_changes = True
    elif key == "preview":
        i = PREVIEW_MODES.index(post.preview) if post.preview in PREVIEW_MODES else 0
        values["preview"] = PREVIEW_MODES[(i + 1) % len(PREVIEW_MODES)]
        look_changes = True
    elif key == "autodel":
        i = AUTODELETE.index(post.delete_after) if post.delete_after in AUTODELETE else 0
        values["delete_after"] = AUTODELETE[(i + 1) % len(AUTODELETE)]
    if not values:
        await cq.answer()
        return
    await P.update_post(app, post.id, **values)
    await cq.answer("Готово")
    await show_settings(app, cq.from_user.id, post.id, edit=clicked(cq), refresh=look_changes)
