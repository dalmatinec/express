"""Карточки наказаний и списки.

Упоминание через tg://user присылает человеку уведомление. Поэтому так ссылаемся только на того,
кого наказали. Админа и строки журнала показываем ссылкой на username: она кликабельна и никого
не беспокоит. ID всегда моноширинным шрифтом: в Telegram он копируется одним нажатием.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from ..texts import Html, t
from .parser import format_duration
from .service import Outcome, Person

LIST_REASON_LIMIT = 60


def mention(person: Person) -> Html:
    return Html(f'<a href="tg://user?id={person.id}">{escape(person.name, quote=False)}</a>')


def quiet_name(name: str, username: str | None) -> Html:
    safe = escape(name, quote=False)
    return Html(f'<a href="https://t.me/{username}">{safe}</a>' if username else safe)


def id_code(user_id: int) -> Html:
    return Html(f"<code>{user_id}</code>")


def progress(count: int, limit: int) -> str:
    filled = min(count, limit)
    return "▰" * filled + "▱" * max(limit - filled, 0)


def format_moment(moment: datetime, offset_hours: int) -> str:
    local = moment.astimezone(timezone(timedelta(hours=offset_hours)))
    return local.strftime("%d.%m %H:%M")


def _term_line(outcome: Outcome, offset_hours: int) -> str:
    if outcome.until is None or outcome.seconds is None:
        return t("card_forever")
    return t(
        "card_term",
        term=format_duration(outcome.seconds),
        until=format_moment(outcome.until, offset_hours),
    )


def _target_line(person: Person) -> str:
    return t("card_target", target=mention(person), id=id_code(person.id))


def _admin_line(admin: Person) -> str:
    if admin.id == 0:
        return t("card_auto")
    return t("card_admin", admin=quiet_name(admin.name, admin.username))


def render_card(outcome: Outcome, offset_hours: int) -> str:
    if outcome.kind == "warn":
        lines = [
            t(
                "card_warn",
                count=outcome.warn_count,
                limit=outcome.warn_limit,
                bar=progress(outcome.warn_count, outcome.warn_limit),
            )
        ]
    else:
        lines = [t(f"card_{outcome.kind}")]
    lines.append(_target_line(outcome.target))
    if outcome.kind in {"ban", "mute"}:
        lines.append(_term_line(outcome, offset_hours))
    if outcome.reason:
        lines.append(t("card_reason", reason=outcome.reason))
    if outcome.escalated is not None:
        lines.append(t(f"card_escalated_{outcome.escalated.kind}"))
        if outcome.escalated.kind != "kick":
            lines.append(_term_line(outcome.escalated, offset_hours))
    lines.append(_admin_line(outcome.admin))
    return "\n".join(lines)


def render_lifted(outcome: Outcome) -> str:
    return "\n".join(
        [
            t(f"card_lifted_{outcome.kind}"),
            _target_line(outcome.target),
            _admin_line(outcome.admin),
        ]
    )


def undo_target(outcome: Outcome) -> int | None:
    """Какую запись снимает кнопка: при лимите предупреждений снимается наказание за него."""
    source = outcome.escalated or outcome
    return None if source.kind == "kick" else source.punishment_id


def _term_of(row: Any) -> str:
    if row["kind"] == "kick":
        return t("term_once")
    if row["expires_at"] is None:
        return t("term_forever")
    seconds = int((row["expires_at"] - row["created_at"]).total_seconds())
    return format_duration(seconds)


def _short_reason(reason: str | None) -> str:
    if not reason:
        return t("no_reason")
    return reason if len(reason) <= LIST_REASON_LIMIT else reason[:LIST_REASON_LIMIT - 1] + "…"


def _details(row: Any, offset_hours: int) -> str:
    admin = (
        quiet_name(row["admin_name"], row["admin_username"])
        if row["admin_name"]
        else Html(t("by_system"))
    )
    return t(
        "list_details",
        date=format_moment(row["created_at"], offset_hours),
        term=_term_of(row),
        reason=_short_reason(row["reason"]),
        admin=admin,
    )


def _icon(row: Any) -> Html:
    return Html(t("kind_revoked") if row["revoked_at"] is not None else t(f"kind_{row['kind']}"))


def _quote(lines: list[str]) -> str:
    return "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


def render_history(target: Person, rows: Iterable[Any], offset_hours: int) -> str:
    title = t("history_title", target=mention(target), id=id_code(target.id))
    lines = [
        t("history_line", icon=_icon(row), details=Html(_details(row, offset_hours)))
        for row in rows
    ]
    return f"{title}\n{_quote(lines)}" if lines else f"{title}\n{t('history_empty')}"


def render_journal(rows: Iterable[Any], offset_hours: int) -> str:
    lines = []
    for row in rows:
        lines.append(
            t(
                "journal_line",
                icon=_icon(row),
                target=quiet_name(row["target_name"] or "ID", row["target_username"]),
                id=id_code(row["user_id"]),
                details=Html(_details(row, offset_hours)),
            )
        )
    title = t("journal_title")
    return f"{title}\n{_quote(lines)}" if lines else f"{title}\n{t('history_empty')}"


def render_whitelist(rows: Iterable[Any]) -> str:
    lines = [
        t(
            "whitelist_line",
            name=quiet_name(row["first_name"] or "ID", row["username"]),
            id=id_code(row["user_id"]),
        )
        for row in rows
    ]
    title = t("whitelist_title", count=len(lines))
    return f"{title}\n{_quote(lines)}" if lines else f"{title}\n{t('whitelist_empty')}"
