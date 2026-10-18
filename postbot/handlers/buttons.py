"""Кнопки поста: добавить, изменить, цвет одной кнопкой по кругу, иконка, двигать стрелками.
Предпросмотр поста сверху меняется сразу."""
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import ui
from ..app import App
from ..models import PostButton
from ..services import posts as P
from ..services.render import edit_markup
from ..states import Btn
from ..utils.layout import grid_of, move
from ..utils.text import (
    COPY_LIMIT, LABEL_LIMIT, POPUP_LIMIT, emoji_html, first_custom_emoji, h, normalize_url, parse_button_list,
    parse_label, short,
)
from .common import clicked, parts
from .editor import open_editor, render

router = Router(name="buttons")
router.message.filter(F.chat.type == "private")

KINDS = {"url": "🔗 открывает ссылку", "copy": "📋 копирует текст", "popup": "💬 показывает всплывающее окно"}
KIND_BUTTONS = {"url": "🔗 Открывает ссылку", "copy": "📋 Копирует текст", "popup": "💬 Всплывающее окно"}
VALUE_NAMES = {"url": "🔗 Ссылка", "copy": "📋 Текст для копирования", "popup": "💬 Текст окна"}
VALUE_ASK = {
    "url": "Пришлите ссылку: https://..., t.me/... или @username.",
    "copy": f"Пришлите текст, который скопируется по нажатию, до {COPY_LIMIT} символов. Например промокод.",
    "popup": f"Пришлите текст всплывающего окна, до {POPUP_LIMIT} символов. Его увидит тот, кто нажмёт кнопку.",
}
BULK_HINT = (
    "📋 Пришлите кнопки списком. Каждая строка - отдельный ряд, кнопки в одном ряду через |\n"
    "Сначала текст кнопки, в конце ссылка:\n\n"
    "<code>Наш канал - https://t.me/channel</code>\n"
    "<code>Чат @chat | Сайт site.com</code>\n\n"
    "Премиум-эмодзи в начале текста станет иконкой кнопки."
)


def label_of(btn: PostButton) -> str:
    return btn.label or "без текста"


async def refresh_preview(app: App, uid: int, post_id: int) -> None:
    """Обновить кнопки в предпросмотре без переотправки."""
    scr = app.screens.get(uid, {})
    if scr.get("preview_post") != post_id or not scr.get("preview"):
        return
    try:
        await edit_markup(app, uid, scr["preview"], await P.get_buttons(app, post_id))
    except TelegramAPIError:
        pass


def grid_rows(buttons: list[PostButton], make_cb, selected: int | None = None) -> list[list]:
    rows: list[list] = []
    current = None
    for btn in buttons:
        if btn.row != current or not rows:
            rows.append([])
            current = btn.row
        text = f"👉 {btn.label}" if btn.id == selected else btn.label
        rows[-1].append(ui.b(text, make_cb(btn), btn.style, btn.icon))
    return rows


def color_name(style: str | None) -> str:
    return ui.STYLES.get(style, ui.STYLES[None]).split(" ", 1)[1].lower()


def next_style(style: str | None) -> str | None:
    """Одна кнопка цвета: каждое нажатие - следующий цвет по кругу."""
    order = list(ui.STYLES)
    i = order.index(style) if style in order else 0
    return order[(i + 1) % len(order)]


async def show_list(app: App, uid: int, post_id: int, edit: Message | None = None, note: str = "",
                    sel: int | None = None) -> None:
    """Кнопки поста как в самом посте. Нажали на кнопку - она выбрана, появились стрелки."""
    buttons = await P.get_buttons(app, post_id)
    if sel and not any(x.id == sel for x in buttons):
        sel = None
    rows = grid_rows(buttons, lambda btn: f"k:{post_id}:sel:{btn.id}", sel)
    if sel:
        chosen = next(x for x in buttons if x.id == sel)
        rows.append([ui.b("⬅️", f"k:{post_id}:md:{sel}:left"), ui.b("⬆️", f"k:{post_id}:md:{sel}:up"),
                     ui.b("⬇️", f"k:{post_id}:md:{sel}:down"), ui.b("➡️", f"k:{post_id}:md:{sel}:right")])
        rows.append([ui.b("↕️ Отдельно", f"k:{post_id}:md:{sel}:solo"),
                     ui.b(f"🎨 Цвет: {color_name(chosen.style)}", f"k:{post_id}:col:{sel}:l", chosen.style)])
        rows.append([ui.b("✏️ Текст, ссылка, иконка", f"k:{post_id}:card:{sel}"),
                     ui.b("🗑 Удалить", f"k:{post_id}:del:{sel}", "danger")])
        rows.append([ui.b("✅ Готово", f"k:{post_id}:list", "success")])
        text = (f"🔘 Выбрана: {h(label_of(chosen))}\n\n"
                "Стрелки двигают её, предпросмотр сверху меняется сразу. В ряду до трёх кнопок.\n"
                "Нажмите другую кнопку, чтобы выбрать её.")
    else:
        rows.append([ui.b("➕ Добавить кнопку", f"k:{post_id}:add", "success"),
                     ui.b("📋 Списком", f"k:{post_id}:bulk")])
        rows.append([ui.b("🔙 К посту", f"e:{post_id}:v")])
        if buttons:
            text = ("🔘 Кнопки поста\n\nОни стоят ровно так, как будут в посте. Нажмите на кнопку, чтобы выбрать её: "
                    "появятся стрелки, цвет и всё остальное.")
        else:
            text = "🔘 Кнопок пока нет.\n\nДобавьте по одной или сразу списком."
    if note:
        text = f"{note}\n\n{text}"
    await render(app, uid, post_id, text, ui.kb(rows), edit=edit)


async def show_card(app: App, uid: int, btn: PostButton, edit: Message | None = None, note: str = "") -> None:
    pid, bid = btn.post_id, btn.id
    value = h(btn.value) if btn.kind != "url" else f'<a href="{h(btn.value)}">{h(short(btn.value, 60))}</a>'
    text = (f"🔘 Кнопка: {h(label_of(btn))}\n\n"
            f"Что делает: {KINDS.get(btn.kind, btn.kind)}\n"
            f"{VALUE_NAMES.get(btn.kind, 'Значение')}: {value}\n"
            f"Цвет: {color_name(btn.style)}\n"
            f"Премиум-иконка: {'есть' if btn.icon else 'нет'}\n\n"
            "Как выглядит 👇")
    if note:
        text = f"{note}\n\n{text}"
    rows = [
        [ui.b(btn.label, "noop", btn.style, btn.icon)],
        [ui.b("✏️ Текст", f"k:{pid}:lbl:{bid}"), ui.b(VALUE_NAMES[btn.kind], f"k:{pid}:val:{bid}")],
        [ui.b("😀 Иконка", f"k:{pid}:ico:{bid}"), ui.b("🎨 Цвет", f"k:{pid}:col:{bid}:c")],
    ]
    if btn.icon:
        rows.append([ui.b("🚫 Убрать иконку", f"k:{pid}:icodel:{bid}")])
    rows.append([ui.b("🔙 Назад", f"k:{pid}:sel:{bid}")])
    await render(app, uid, pid, text, ui.kb(rows), edit=edit)


async def _ask(app: App, uid: int, text: str, back: str, extra: list | None = None) -> None:
    rows = [extra] if extra else []
    rows.append([ui.b("🔙 Назад", back)])
    await ui.show(app, uid, text, ui.kb(rows), keep_preview=True)


@router.callback_query(F.data.startswith("k:"))
async def buttons_cb(cq: CallbackQuery, state: FSMContext, app: App) -> None:
    p = parts(cq)
    pid, action = int(p[1]), p[2]
    bid = int(p[3]) if len(p) > 3 and p[3].isdigit() else 0
    uid, msg = cq.from_user.id, clicked(cq)
    post = await P.get_post(app, pid)
    if post is None:
        await cq.answer("Пост уже удалён", show_alert=True)
        await open_editor(app, uid, pid, edit=msg)
        return
    btn = await P.get_button(app, bid) if bid else None
    if bid and (btn is None or btn.post_id != pid):
        await cq.answer("Кнопки уже нет")
        await show_list(app, uid, pid, edit=msg)
        return
    data = await state.get_data()
    await state.clear()

    if action == "list":
        await cq.answer()
        await show_list(app, uid, pid, edit=msg)

    elif action == "add":
        await cq.answer()
        await state.set_state(Btn.label)
        await state.update_data(post_id=pid)
        await _ask(app, uid, "➕ Пришлите текст новой кнопки.\nЕсли в начале поставить премиум-эмодзи, "
                             "оно станет иконкой кнопки.", f"k:{pid}:list")

    elif action == "addk":
        kind = p[3]
        if kind not in KINDS or not (data.get("label") or data.get("icon")):
            await cq.answer()
            await show_list(app, uid, pid, edit=msg)
            return
        await cq.answer()
        await state.set_state(Btn.value)
        await state.update_data(post_id=pid, kind=kind, label=data.get("label", ""), icon=data.get("icon"))
        await _ask(app, uid, VALUE_ASK[kind], f"k:{pid}:list")

    elif action == "bulk":
        await cq.answer()
        await state.set_state(Btn.bulk)
        await state.update_data(post_id=pid)
        await _ask(app, uid, BULK_HINT, f"k:{pid}:list")

    elif action == "card":
        await cq.answer()
        await show_card(app, uid, btn, edit=msg)

    elif action in ("sel", "mv"):
        await cq.answer()
        await show_list(app, uid, pid, edit=msg, sel=bid)

    elif action == "col":
        style = next_style(btn.style)
        await P.update_button(app, bid, style=style)
        await cq.answer(f"Цвет: {color_name(style)}")
        await refresh_preview(app, uid, pid)
        if len(p) > 4 and p[4] == "c":
            await show_card(app, uid, await P.get_button(app, bid), edit=msg)
        else:
            await show_list(app, uid, pid, edit=msg, sel=bid)

    elif action == "lbl":
        await cq.answer()
        await state.set_state(Btn.edit_label)
        await state.update_data(post_id=pid, button_id=bid)
        await _ask(app, uid, f"✏️ Сейчас: {h(btn.label)}\n\nПришлите новый текст кнопки. Премиум-эмодзи в начале "
                             "станет иконкой.", f"k:{pid}:card:{bid}")

    elif action == "val":
        await cq.answer()
        await state.set_state(Btn.edit_value)
        await state.update_data(post_id=pid, button_id=bid)
        await _ask(app, uid, f"Сейчас: {h(btn.value)}\n\n{VALUE_ASK[btn.kind]}", f"k:{pid}:card:{bid}")

    elif action == "ico":
        await cq.answer()
        await state.set_state(Btn.icon)
        await state.update_data(post_id=pid, button_id=bid)
        await _ask(app, uid, "✨ Пришлите одно премиум-эмодзи, оно встанет иконкой перед текстом кнопки.",
                   f"k:{pid}:card:{bid}")

    elif action == "icodel":
        await P.update_button(app, bid, icon=None)
        await cq.answer("Иконка убрана")
        await refresh_preview(app, uid, pid)
        await show_card(app, uid, await P.get_button(app, bid), edit=msg)

    elif action == "del":
        await P.delete_button(app, bid)
        await cq.answer("Кнопка удалена")
        await refresh_preview(app, uid, pid)
        await show_list(app, uid, pid, edit=msg)

    elif action == "md":
        grid = grid_of(await P.get_buttons(app, pid))
        new = move(grid, bid, p[4])
        if new == grid:
            await cq.answer("Дальше некуда")
            return
        await P.save_grid(app, new)
        await cq.answer()
        await refresh_preview(app, uid, pid)
        await show_list(app, uid, pid, edit=msg, sel=bid)
    else:
        await cq.answer()


# ---------- ввод ----------

async def _data(state: FSMContext) -> tuple[int, int]:
    data = await state.get_data()
    return data.get("post_id", 0), data.get("button_id", 0)


@router.message(Btn.label)
async def got_label(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    pid, _ = await _data(state)
    await ui.drop_input(message)
    label, icon = parse_label(message)
    if not label and not icon:
        await _ask(app, uid, "⚠️ Нужен текст кнопки.", f"k:{pid}:list")
        return
    await state.update_data(label=label, icon=icon)
    shown = f"{emoji_html(icon)} {h(label)}".strip()
    rows = [[ui.b(text, f"k:{pid}:addk:{k}")] for k, text in KIND_BUTTONS.items()]
    rows.append([ui.b("🔙 Назад", f"k:{pid}:list")])
    await ui.show(app, uid, f"Кнопка: {shown}\n\nЧто она будет делать?", ui.kb(rows), keep_preview=True)


def _check_value(kind: str, message: Message) -> tuple[str | None, str]:
    """-> (значение, ошибка)."""
    text = (message.text or "").strip()
    if not text:
        return None, "⚠️ Пришлите текстом."
    if kind == "url":
        url = normalize_url(text)
        return (url, "") if url else (None, "⚠️ Не похоже на ссылку. Пример: https://t.me/channel или @channel")
    limit = COPY_LIMIT if kind == "copy" else POPUP_LIMIT
    if len(text) > limit:
        return None, f"⚠️ Длинновато: {len(text)} символов, можно до {limit}."
    return text, ""


@router.message(Btn.value)
async def got_value(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    pid, kind = data.get("post_id", 0), data.get("kind", "url")
    await ui.drop_input(message)
    value, error = _check_value(kind, message)
    if value is None:
        await _ask(app, uid, f"{error}\n\n{VALUE_ASK[kind]}", f"k:{pid}:list")
        return
    bid = await P.add_button(app, pid, kind, data.get("label", ""), data.get("icon"), value)
    await state.clear()
    await refresh_preview(app, uid, pid)
    await show_list(app, uid, pid, note="✅ Кнопка добавлена. Цвет и место можно поменять здесь.", sel=bid)


@router.message(Btn.bulk)
async def got_bulk(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    pid, _ = await _data(state)
    await ui.drop_input(message)
    rows, errors = parse_button_list(message)
    if not rows:
        err = "\n".join(h(e) for e in errors[:5]) or "пусто"
        await _ask(app, uid, f"⚠️ Не нашёл ни одной кнопки:\n{err}\n\n{BULK_HINT}", f"k:{pid}:list")
        return
    count = await P.add_rows(app, pid, rows)
    await state.clear()
    await refresh_preview(app, uid, pid)
    note = f"✅ Добавлено кнопок: {count}"
    if errors:
        note += "\nПропустил строки:\n" + "\n".join(h(e) for e in errors[:5])
    await show_list(app, uid, pid, note=note)


@router.message(Btn.edit_label)
async def got_edit_label(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    pid, bid = await _data(state)
    await ui.drop_input(message)
    label, icon = parse_label(message)
    if not label and not icon:
        await _ask(app, uid, "⚠️ Нужен текст кнопки.", f"k:{pid}:card:{bid}")
        return
    values = {"label": label[:LABEL_LIMIT]}
    if icon:
        values["icon"] = icon
    await P.update_button(app, bid, **values)
    await state.clear()
    await refresh_preview(app, uid, pid)
    btn = await P.get_button(app, bid)
    if btn:
        await show_card(app, uid, btn, note="✏️ Текст обновлён.")


@router.message(Btn.edit_value)
async def got_edit_value(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    pid, bid = await _data(state)
    await ui.drop_input(message)
    btn = await P.get_button(app, bid)
    if btn is None:
        await state.clear()
        await show_list(app, uid, pid)
        return
    value, error = _check_value(btn.kind, message)
    if value is None:
        await _ask(app, uid, f"{error}\n\n{VALUE_ASK[btn.kind]}", f"k:{pid}:card:{bid}")
        return
    await P.update_button(app, bid, value=value)
    await state.clear()
    await refresh_preview(app, uid, pid)
    await show_card(app, uid, await P.get_button(app, bid), note="✅ Сохранено.")


@router.message(Btn.icon)
async def got_icon(message: Message, state: FSMContext, app: App) -> None:
    uid = message.from_user.id
    pid, bid = await _data(state)
    await ui.drop_input(message)
    icon = first_custom_emoji(message)
    if icon is None and message.sticker and message.sticker.custom_emoji_id:
        icon = message.sticker.custom_emoji_id
    if icon is None:
        await _ask(app, uid, "⚠️ Это не премиум-эмодзи. Нужно именно эмодзи из премиум-набора.",
                   f"k:{pid}:card:{bid}")
        return
    await P.update_button(app, bid, icon=icon)
    await state.clear()
    await refresh_preview(app, uid, pid)
    btn = await P.get_button(app, bid)
    if btn:
        await show_card(app, uid, btn, note="✨ Иконка поставлена.")
