"""Работа с текстом: HTML с премиум-эмодзи, подписи кнопок, ссылки, список кнопок одним сообщением."""
import re
from html import escape, unescape

from aiogram.types import Message, MessageEntity

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
LABEL_LIMIT = 64
POPUP_LIMIT = 200
COPY_LIMIT = 256


def html_to_plain(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


def message_html(message: Message) -> str:
    """Текст или подпись как HTML. Форматирование и премиум-эмодзи остаются."""
    if not (message.text or message.caption):
        return ""
    return message.html_text


def short(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def auto_title(html: str) -> str:
    plain = html_to_plain(html).strip()
    first = next((line for line in plain.splitlines() if line.strip()), "")
    return short(first, 40) if first else "Пост без текста"


def h(text: str | None) -> str:
    return escape(text or "")


def emoji_html(icon: str | None, fallback: str = "⭐️") -> str:
    return f'<tg-emoji emoji-id="{icon}">{fallback}</tg-emoji>' if icon else ""


# ---------- премиум-эмодзи внутри текста ----------

def _u16_offsets(text: str) -> list[int]:
    """Смещение каждого символа в UTF-16 (Telegram считает так). Последний элемент - длина."""
    out = [0]
    for ch in text:
        out.append(out[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return out


def _take_icon(text: str, entities: list[MessageEntity], start: int, end: int,
               pos: list[int]) -> tuple[str, str | None]:
    """Кусок text[start:end]. Первое премиум-эмодзи в нём вынимается и становится иконкой."""
    s16, e16 = pos[start], pos[end]
    for e in entities:
        if e.type == "custom_emoji" and e.custom_emoji_id and s16 <= e.offset < e16:
            a = pos.index(e.offset)
            b = pos.index(e.offset + e.length) if (e.offset + e.length) in pos else a + 1
            return text[start:a] + text[b:end], e.custom_emoji_id
    return text[start:end], None


def parse_label(message: Message) -> tuple[str, str | None]:
    """Подпись кнопки: первое премиум-эмодзи станет иконкой, остальное текстом."""
    text = message.text or ""
    label, icon = _take_icon(text, list(message.entities or []), 0, len(text), _u16_offsets(text))
    return " ".join(label.split())[:LABEL_LIMIT], icon


def first_custom_emoji(message: Message) -> str | None:
    for e in list(message.entities or []) + list(message.caption_entities or []):
        if e.type == "custom_emoji" and e.custom_emoji_id:
            return e.custom_emoji_id
    return None


def normalize_url(raw: str) -> str | None:
    raw = raw.strip()
    if re.fullmatch(r"@[A-Za-z0-9_]{4,32}", raw):
        return f"https://t.me/{raw[1:]}"
    if re.match(r"^(t\.me|telegram\.me)/\S+$", raw):
        return f"https://{raw}"
    if re.match(r"^(https?|tg)://\S+$", raw):
        return raw
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", raw):  # просто site.com/page
        return f"https://{raw}"
    return None


ParsedButton = tuple[str, str, str | None]  # подпись, ссылка, иконка


def parse_button_list(message: Message) -> tuple[list[list[ParsedButton]], list[str]]:
    """Кнопки одним сообщением. Каждая строка - ряд, кнопки в ряду через |.
    Внутри кнопки сначала текст, в конце ссылка, между ними можно поставить тире:
        Наш канал - https://t.me/channel | Чат @chat
        Сайт site.com
    """
    text = message.text or ""
    entities = list(message.entities or [])
    pos = _u16_offsets(text)
    rows: list[list[ParsedButton]] = []
    errors: list[str] = []
    line_start = 0
    for line in text.split("\n"):
        row: list[ParsedButton] = []
        seg_start = line_start
        for seg in line.split("|"):
            chunk, icon = _take_icon(text, entities, seg_start, seg_start + len(seg), pos)
            seg_start += len(seg) + 1
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.rsplit(None, 1)
            url = normalize_url(parts[-1]) if parts else None
            label = parts[0] if len(parts) == 2 else ""
            label = " ".join(label.rstrip(" -").split())[:LABEL_LIMIT]
            if url is None:
                errors.append(f"нет ссылки в конце: {short(chunk, 30)}")
                continue
            if not label and not icon:
                errors.append(f"нет текста кнопки: {short(chunk, 30)}")
                continue
            row.append((label, url, icon))
        if row:
            rows.append(row[:8])
        line_start += len(line) + 1
    return rows, errors
