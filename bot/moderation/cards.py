"""Маленькие карточки наказаний и списки журнала."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from ..texts import Html, t
from .parser import format_duration
from .service import Outcome, Person


def mention(person: Person) -> Html:
    return Html(f'<a href="tg://user?id={person.id}">{escape(person.name, quote=False)}</a>')


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


def render_card(outcome: Outcome, offset_hours: int) -> str:
    target = mention(outcome.target)
    if outcome.kind == "warn":
        lines = [
            t("card_warn", target=target, count=outcome.warn_count, limit=outcome.warn_limit)
        ]
    else:
        lines = [t(f"card_{outcome.kind}", target=target)]
        if outcome.kind != "kick":
            lines.append(_term_line(outcome, offset_hours))
    if outcome.reason:
        lines.append(t("card_reason", reason=outcome.reason))
    if outcome.escalated is not None:
        lines.append(t(f"card_escalated_{outcome.escalated.kind}"))
        if outcome.escalated.kind != "kick":
            lines.append(_term_line(outcome.escalated, offset_hours))
    lines.append(t("card_admin", admin=mention(outcome.admin)))
    return "\n".join(lines)


def render_lifted(outcome: Outcome) -> str:
    return "\n".join(
        [
            t(f"card_lifted_{outcome.kind}", target=mention(outcome.target)),
            t("card_admin", admin=mention(outcome.admin)),
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


def _common_fields(row: Any, offset_hours: int) -> dict[str, Any]:
    icon = t("kind_revoked") if row["revoked_at"] is not None else t(f"kind_{row['kind']}")
    return {
        "icon": Html(icon),
        "date": format_moment(row["created_at"], offset_hours),
        "term": _term_of(row),
        "reason": row["reason"] or t("no_reason"),
        "admin": row["admin_name"] or t("by_system"),
    }


def render_history(target: Person, rows: Iterable[Any], offset_hours: int) -> str:
    lines = [t("history_title", target=mention(target))]
    lines += [t("history_line", **_common_fields(row, offset_hours)) for row in rows]
    if len(lines) == 1:
        lines.append(t("history_empty"))
    return "\n".join(lines)


def render_journal(rows: Iterable[Any], offset_hours: int) -> str:
    lines = [t("journal_title")]
    for row in rows:
        name = row["target_name"] or f"ID {row['user_id']}"
        lines.append(t("journal_line", target=name, **_common_fields(row, offset_hours)))
    if len(lines) == 1:
        lines.append(t("history_empty"))
    return "\n".join(lines)
