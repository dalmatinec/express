"""Посты и их кнопки в базе. Черновик - это обычный пост с is_draft = 1.
Правка готового поста идёт в копии (source_id = id оригинала), при сохранении копия переезжает в оригинал."""
import secrets
from typing import Any

from ..app import App, now
from ..models import Post, PostButton, Publication, from_row
from ..utils.layout import Grid, grid_of, rows_of

POST_FIELDS = ("title", "html", "media_id", "silent", "protect", "spoiler", "caption_above", "preview",
               "preview_above", "pin", "delete_after")


def new_token() -> str:
    return secrets.token_hex(5)


async def get_post(app: App, post_id: int) -> Post | None:
    row = await app.db.fetchone("SELECT * FROM posts WHERE id = ?", (post_id,))
    return from_row(Post, row) if row else None


async def get_buttons(app: App, post_id: int) -> list[PostButton]:
    rows = await app.db.fetchall("SELECT * FROM post_buttons WHERE post_id = ? ORDER BY row, position, id",
                                 (post_id,))
    return [from_row(PostButton, r) for r in rows]


async def get_button(app: App, button_id: int) -> PostButton | None:
    row = await app.db.fetchone("SELECT * FROM post_buttons WHERE id = ?", (button_id,))
    return from_row(PostButton, row) if row else None


async def create_draft(app: App, author_id: int, html: str = "", media_id: int | None = None) -> int:
    ts = now()
    return await app.db.execute(
        "INSERT INTO posts(html, media_id, author_id, is_draft, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
        (html, media_id, author_id, ts, ts))


async def update_post(app: App, post_id: int, **values: Any) -> None:
    bad = set(values) - set(POST_FIELDS)
    assert not bad, bad
    cols = ", ".join(f"{k} = ?" for k in values)
    await app.db.execute(f"UPDATE posts SET {cols}, updated_at = ? WHERE id = ?", (*values.values(), now(), post_id))


async def find_new_draft(app: App, author_id: int) -> Post | None:
    """Незаконченный новый пост этого человека (не правка старого)."""
    row = await app.db.fetchone(
        "SELECT * FROM posts WHERE is_draft = 1 AND source_id IS NULL AND author_id = ? ORDER BY id DESC LIMIT 1",
        (author_id,))
    return from_row(Post, row) if row else None


async def drop_draft(app: App, post_id: int) -> None:
    await app.db.execute("DELETE FROM posts WHERE id = ? AND is_draft = 1", (post_id,))


async def clone_post(app: App, post_id: int, author_id: int, *, as_edit: bool) -> int:
    """Копия поста. as_edit=True - черновик для правки этого поста, иначе новый независимый черновик."""
    post = await get_post(app, post_id)
    assert post is not None
    ts = now()
    title = post.title if as_edit else (f"{post.title} (копия)" if post.title else "")
    new_id = await app.db.execute(
        "INSERT INTO posts(title, html, media_id, silent, protect, spoiler, caption_above, preview, preview_above, "
        "pin, delete_after, is_draft, source_id, author_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
        (title, post.html, post.media_id, post.silent, post.protect, post.spoiler, post.caption_above, post.preview,
         post.preview_above, post.pin, post.delete_after, post_id if as_edit else None, author_id, ts, ts))
    await app.db.executemany(
        "INSERT INTO post_buttons(post_id, token, kind, label, icon, style, value, row, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ((new_id, b.token if as_edit else new_token(), b.kind, b.label, b.icon, b.style, b.value, b.row, b.position)
         for b in await get_buttons(app, post_id)))
    return new_id


async def commit_draft(app: App, draft_id: int) -> int:
    """Черновик в историю. -> id готового поста."""
    draft = await get_post(app, draft_id)
    assert draft is not None
    if not draft.is_draft:
        return draft.id
    if draft.source_id and await get_post(app, draft.source_id):
        target = draft.source_id
        await update_post(app, target, **{k: getattr(draft, k) for k in POST_FIELDS})
        await app.db.execute("DELETE FROM post_buttons WHERE post_id = ?", (target,))
        await app.db.execute("UPDATE post_buttons SET post_id = ? WHERE post_id = ?", (target, draft_id))
        await app.db.execute("DELETE FROM posts WHERE id = ?", (draft_id,))
        return target
    await app.db.execute("UPDATE posts SET is_draft = 0, source_id = NULL, updated_at = ? WHERE id = ?",
                         (now(), draft_id))
    return draft_id


async def delete_post(app: App, post_id: int) -> None:
    await app.db.execute("DELETE FROM posts WHERE id = ? OR (source_id = ? AND is_draft = 1)", (post_id, post_id))


async def list_posts(app: App) -> list[Post]:
    rows = await app.db.fetchall("SELECT * FROM posts WHERE is_draft = 0 ORDER BY id DESC")
    return [from_row(Post, r) for r in rows]


# ---------- кнопки ----------

async def add_button(app: App, post_id: int, kind: str, label: str, icon: str | None, value: str,
                     style: str | None = None) -> int:
    last_row = await app.db.fetchval("SELECT MAX(row) FROM post_buttons WHERE post_id = ?", (post_id,))
    row = 0 if last_row is None else last_row + 1
    return await app.db.execute(
        "INSERT INTO post_buttons(post_id, token, kind, label, icon, style, value, row, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)", (post_id, new_token(), kind, label, icon, style, value, row))


async def add_rows(app: App, post_id: int, rows: list[list[tuple[str, str, str | None]]],
                   kind: str = "url") -> int:
    """Добавляет готовые ряды кнопок в конец. -> сколько кнопок добавлено."""
    last_row = await app.db.fetchval("SELECT MAX(row) FROM post_buttons WHERE post_id = ?", (post_id,))
    start = 0 if last_row is None else last_row + 1
    data = [(post_id, new_token(), kind, label, icon, None, value, start + r, p)
            for r, row in enumerate(rows) for p, (label, value, icon) in enumerate(row)]
    await app.db.executemany(
        "INSERT INTO post_buttons(post_id, token, kind, label, icon, style, value, row, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", data)
    return len(data)


async def import_buttons(app: App, post_id: int, rows: list[list[dict]]) -> int:
    """Кнопки из пересланного поста: [{kind, label, icon, style, value}]."""
    data = [(post_id, new_token(), b["kind"], b["label"], b.get("icon"), b.get("style"), b["value"], r, p)
            for r, row in enumerate(rows) for p, b in enumerate(row)]
    await app.db.executemany(
        "INSERT INTO post_buttons(post_id, token, kind, label, icon, style, value, row, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", data)
    return len(data)


async def update_button(app: App, button_id: int, **values: Any) -> None:
    bad = set(values) - {"kind", "label", "icon", "style", "value"}
    assert not bad, bad
    cols = ", ".join(f"{k} = ?" for k in values)
    await app.db.execute(f"UPDATE post_buttons SET {cols} WHERE id = ?", (*values.values(), button_id))


async def delete_button(app: App, button_id: int) -> None:
    btn = await get_button(app, button_id)
    await app.db.execute("DELETE FROM post_buttons WHERE id = ?", (button_id,))
    if btn:
        await save_grid(app, grid_of(await get_buttons(app, btn.post_id)))


async def save_grid(app: App, grid: Grid) -> None:
    await app.db.executemany("UPDATE post_buttons SET row = ?, position = ? WHERE id = ?", rows_of(grid))


async def popup_text(app: App, token: str) -> str | None:
    """Текст всплывашки. Сначала ищем в готовых постах, потом в черновиках (для предпросмотра)."""
    return await app.db.fetchval(
        "SELECT b.value FROM post_buttons b JOIN posts p ON p.id = b.post_id "
        "WHERE b.token = ? AND b.kind = 'popup' ORDER BY p.is_draft, b.id DESC LIMIT 1", (token,))


# ---------- публикации ----------

async def publications(app: App, post_id: int, status: str | None = None) -> list[Publication]:
    sql = "SELECT * FROM publications WHERE post_id = ?"
    params: list[Any] = [post_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = await app.db.fetchall(sql + " ORDER BY id", params)
    return [from_row(Publication, r) for r in rows]
