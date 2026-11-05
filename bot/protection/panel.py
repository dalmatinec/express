"""Панель защиты в группе: одно сообщение с переключателями."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..moderation.parser import format_duration
from ..texts import t
from .settings import ProtectionSettings

PANEL_PREFIX = "prot:"


def _state(enabled: bool) -> str:
    return t("state_on") if enabled else t("state_off")


def render_panel(settings: ProtectionSettings) -> tuple[str, InlineKeyboardMarkup]:
    flood = (
        t(
            "panel_flood_on",
            limit=settings.flood_limit,
            window=settings.flood_window,
            action=t(f"action_{settings.flood_action}"),
            term=format_duration(settings.flood_seconds),
        )
        if settings.flood_enabled
        else t("state_off")
    )
    links = (
        t("state_off") if settings.link_mode == "off" else t(f"panel_links_{settings.link_mode}")
    )
    text = "\n".join(
        [
            t("panel_title"),
            "",
            t("panel_flood", state=flood),
            t("panel_links", state=links),
            t("panel_mentions", state=_state(bool(settings.mention_limit))),
            t("panel_duplicates", state=_state(bool(settings.duplicate_limit))),
            t("panel_forwards", state=_state(settings.block_forwards)),
            t("panel_action", state=t(f"state_{settings.spam_action}")),
            "",
            t("panel_hint"),
        ]
    )
    links_button = (
        t("state_off")
        if settings.link_mode == "off"
        else t(f"btn_links_{settings.link_mode}")
    )

    def button(label: str, field: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=label, callback_data=f"{PANEL_PREFIX}{field}")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(t("btn_flood", state=_state(settings.flood_enabled)), "flood"),
                button(t("btn_links", state=links_button), "links"),
            ],
            [
                button(t("btn_mentions", state=_state(bool(settings.mention_limit))), "mentions"),
                button(
                    t("btn_duplicates", state=_state(bool(settings.duplicate_limit))),
                    "duplicates",
                ),
            ],
            [
                button(t("btn_forwards", state=_state(settings.block_forwards)), "forwards"),
                button(t("btn_action", state=t(f"btn_action_{settings.spam_action}")), "action"),
            ],
        ]
    )
    return text, keyboard
