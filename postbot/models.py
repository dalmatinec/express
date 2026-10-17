"""Простые классы под строки из базы."""
from dataclasses import dataclass, fields
from typing import Any, TypeVar

T = TypeVar("T")


def from_row(cls: type[T], row: Any) -> T:
    keys = row.keys()
    return cls(**{f.name: row[f.name] for f in fields(cls) if f.name in keys})  # type: ignore[arg-type]


@dataclass(slots=True)
class Post:
    id: int
    title: str
    html: str
    media_id: int | None
    silent: int
    protect: int
    spoiler: int
    caption_above: int
    preview: str
    preview_above: int
    pin: int
    delete_after: int
    is_draft: int
    source_id: int | None
    author_id: int | None
    created_at: int
    updated_at: int

    @property
    def name(self) -> str:
        return self.title or "без названия"


@dataclass(slots=True)
class PostButton:
    id: int
    post_id: int
    token: str
    kind: str
    label: str
    icon: str | None
    style: str | None
    value: str
    row: int
    position: int


@dataclass(slots=True)
class Channel:
    id: int
    title: str
    username: str | None
    type: str
    can_post: int
    can_edit: int
    can_delete: int
    can_pin: int
    is_present: int
    added_by: int | None
    updated_at: int

    @property
    def ok(self) -> bool:
        return bool(self.is_present and self.can_post)

    @property
    def kind_name(self) -> str:
        return "канал" if self.type == "channel" else "группа"

    def post_link(self, message_id: int) -> str | None:
        if self.username:
            return f"https://t.me/{self.username}/{message_id}"
        raw = str(self.id)
        if raw.startswith("-100"):
            return f"https://t.me/c/{raw[4:]}/{message_id}"
        return None


@dataclass(slots=True)
class Publication:
    id: int
    post_id: int
    channel_id: int
    message_id: int | None
    status: str
    run_at: int | None
    sent_at: int | None
    delete_at: int | None
    error: str | None
    by_user: int | None
    created_at: int


@dataclass(slots=True)
class Admin:
    user_id: int
    name: str
    all_channels: int
    can_channels: int
    added_by: int | None
    created_at: int
