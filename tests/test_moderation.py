from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from bot.admins import OWNER_RIGHTS, AdminCache, AdminRights
from bot.moderation.cards import render_card, render_history, render_journal, undo_target
from bot.moderation.parser import format_duration, parse_args, parse_duration
from bot.moderation.service import (
    Moderation,
    ModerationError,
    ModSettings,
    Outcome,
    Person,
    classify_telegram_error,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
CHAT = -100
BOT_ID = 777
ADMIN = Person(1, "Админ")
TARGET = Person(5, "Вася")


# --- Разбор команды ---


@pytest.mark.parametrize(
    "token,seconds",
    [
        ("30с", 30), ("30s", 30), ("10м", 600), ("10m", 600), ("10мин", 600), ("2ч", 7200),
        ("2H", 7200), ("3д", 259200), ("1н", 604800), ("1w", 604800), ("1мес", 2592000),
        ("1д12ч", 129600), ("1h30m", 5400), ("2часа", 7200),
    ],
)
def test_parse_duration(token, seconds):
    assert parse_duration(token) == seconds


@pytest.mark.parametrize("token", ["спам", "2", "2х", "ч", "", "2ч спам", "0ч", "1д2", "x1ч"])
def test_parse_duration_rejects(token):
    assert parse_duration(token) is None


@pytest.mark.parametrize(
    "args,expected",
    [
        (None, (None, None, None)),
        ("2ч спам в чате", (None, 7200, "спам в чате")),
        ("спам в чате", (None, None, "спам в чате")),
        ("@vasya 2ч спам", ("@vasya", 7200, "спам")),
        ("123456789 7д", ("123456789", 604800, None)),
        ("2 часа флуд", (None, 7200, "флуд")),
        ("5 минут", (None, 300, None)),
        ("навсегда реклама", (None, None, "реклама")),
        ("@vasya навсегда", ("@vasya", None, None)),
        ("5 раз написал", (None, None, "5 раз написал")),  # короткое число это не ID
        ("@bad спам", (None, None, "@bad спам")),  # слишком короткий username
    ],
)
def test_parse_args(args, expected):
    parsed = parse_args(args, with_duration=True)
    assert (parsed.target, parsed.seconds, parsed.reason) == expected
    assert parsed.error is None


def test_parse_args_without_duration_keeps_everything_as_reason():
    parsed = parse_args("2ч спам", with_duration=False)
    assert parsed.seconds is None
    assert parsed.reason == "2ч спам"


def test_parse_args_known_target_does_not_take_id_from_text():
    parsed = parse_args("123456 реклама", with_duration=False, has_target=True)
    assert parsed.target is None
    assert parsed.reason == "123456 реклама"


@pytest.mark.parametrize("args,error", [("10с", "too_short"), ("400д", "too_long")])
def test_parse_args_out_of_telegram_limits(args, error):
    assert parse_args(args, with_duration=True).error == error


def test_reason_is_limited_and_single_line():
    parsed = parse_args("причина\nс переносом " + "x" * 500, with_duration=False)
    assert "\n" not in parsed.reason
    assert len(parsed.reason) == 200


@pytest.mark.parametrize(
    "seconds,text",
    [(30, "30 сек"), (600, "10 мин"), (7200, "2 ч"), (129600, "1 д 12 ч"), (90061, "1 д 1 ч")],
)
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


# --- Ошибки Telegram ---


@pytest.mark.parametrize(
    "message,code",
    [
        ("Bad Request: not enough rights to restrict/unrestrict chat member", "bot_no_rights"),
        ("Bad Request: CHAT_ADMIN_REQUIRED", "bot_no_rights"),
        ("Bad Request: user is an administrator of the chat", "target_admin"),
        ("Bad Request: can't remove chat owner", "target_admin"),
        ("Bad Request: USER_NOT_PARTICIPANT", "not_in_chat"),
        ("Bad Request: user not found", "user_not_found"),
        ("Bad Request: PARTICIPANT_ID_INVALID", "user_not_found"),
        ("Bad Request: can't restrict self", "target_bot"),
        ("Bad Request: что-то новое", "tg_error"),
    ],
)
def test_classify_telegram_error(message, code):
    assert classify_telegram_error(message) == code


# --- Логика ---


class FakeTelegram:
    bot_id = BOT_ID

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail: ModerationError | None = None

    def _hit(self, *call: Any) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append(call)

    async def ban(self, chat_id, user_id, until):
        self._hit("ban", user_id, until)

    async def unban(self, chat_id, user_id):
        self._hit("unban", user_id)

    async def mute(self, chat_id, user_id, until):
        self._hit("mute", user_id, until)

    async def unmute(self, chat_id, user_id):
        self._hit("unmute", user_id)

    async def kick(self, chat_id, user_id):
        self._hit("kick", user_id)


class FakeRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail_record = False

    async def record(
        self, bot_id, chat_id, user_id, kind, admin_id, reason, expires, source="manual"
    ):
        if self.fail_record:
            raise ConnectionError("база недоступна")
        self.rows.append({
            "id": len(self.rows) + 1, "user_id": user_id, "kind": kind, "admin_id": admin_id,
            "reason": reason, "expires_at": expires, "source": source, "revoked_at": None,
            "first_name": "Вася",
        })
        return self.rows[-1]["id"]

    def _active(self, user_id, kind):
        return [
            r for r in self.rows
            if r["user_id"] == user_id and r["kind"] == kind and r["revoked_at"] is None
        ]

    async def revoke_active(self, bot_id, chat_id, user_id, kind, by):
        rows = self._active(user_id, kind)
        for row in rows:
            row["revoked_at"] = NOW
        return bool(rows)

    async def count_warns(self, bot_id, chat_id, user_id):
        return len(self._active(user_id, "warn"))

    async def revoke_last_warn(self, bot_id, chat_id, user_id, by):
        rows = self._active(user_id, "warn")
        if not rows:
            return None
        rows[-1]["revoked_at"] = NOW
        return rows[-1]["id"]

    async def get(self, punishment_id, bot_id, chat_id):
        return next((r for r in self.rows if r["id"] == punishment_id), None)

    async def revoke_by_id(self, punishment_id, by):
        row = await self.get(punishment_id, BOT_ID, CHAT)
        if row is None or row["revoked_at"] is not None:
            return False
        row["revoked_at"] = NOW
        return True


def _setup(admins: dict[int, AdminRights] | None = None):
    rights = {
        ADMIN.id: OWNER_RIGHTS,
        BOT_ID: OWNER_RIGHTS,
    } if admins is None else admins

    async def fetch(bot, chat_id):
        return [
            SimpleNamespace(
                status="creator" if r.is_owner else "administrator",
                user=SimpleNamespace(id=user_id),
                can_restrict_members=r.can_restrict,
            )
            for user_id, r in rights.items()
        ]

    repo = FakeRepo()
    return Moderation(repo, AdminCache(fetch), now=lambda: NOW), repo, FakeTelegram()


BOT = SimpleNamespace(id=BOT_ID)


def test_mute_with_term_sets_until_and_journal():
    async def scenario():
        service, repo, tg = _setup()
        outcome = await service.punish(tg, CHAT, "mute", TARGET, ADMIN, seconds=7200, reason="спам")
        until = NOW + timedelta(hours=2)
        assert tg.calls == [("mute", 5, until)]
        assert outcome.until == until and outcome.punishment_id == 1
        assert repo.rows[0]["expires_at"] == until and repo.rows[0]["reason"] == "спам"

    asyncio.run(scenario())


def test_ban_without_term_is_forever():
    async def scenario():
        service, repo, tg = _setup()
        outcome = await service.punish(tg, CHAT, "ban", TARGET, ADMIN)
        assert tg.calls == [("ban", 5, None)]
        assert outcome.until is None and repo.rows[0]["expires_at"] is None

    asyncio.run(scenario())


def test_telegram_refusal_writes_nothing_to_journal():
    async def scenario():
        service, repo, tg = _setup()
        tg.fail = ModerationError("target_admin")
        with pytest.raises(ModerationError):
            await service.punish(tg, CHAT, "ban", TARGET, ADMIN)
        assert repo.rows == []

    asyncio.run(scenario())


def test_journal_failure_does_not_hide_done_action():
    async def scenario():
        service, repo, tg = _setup()
        repo.fail_record = True
        outcome = await service.punish(tg, CHAT, "ban", TARGET, ADMIN, reason="спам")
        assert tg.calls and outcome.punishment_id is None
        assert undo_target(outcome) is None  # кнопку снятия не к чему привязать

    asyncio.run(scenario())


def test_warns_count_up_then_escalate_and_reset():
    async def scenario():
        service, repo, tg = _setup()
        settings = ModSettings()
        first = await service.warn(tg, CHAT, TARGET, ADMIN, settings, reason="a")
        second = await service.warn(tg, CHAT, TARGET, ADMIN, settings, reason="b")
        assert (first.warn_count, second.warn_count) == (1, 2)
        assert first.escalated is None and tg.calls == []

        third = await service.warn(tg, CHAT, TARGET, ADMIN, settings, reason="c")
        assert third.warn_count == 3
        assert third.escalated.kind == "mute"
        assert tg.calls == [("mute", 5, NOW + timedelta(days=1))]
        assert await repo.count_warns(BOT_ID, CHAT, 5) == 0
        assert repo.rows[-1]["source"] == "warn_limit"
        # Кнопка на карточке снимает мут, а не последнее предупреждение.
        assert undo_target(third) == repo.rows[-1]["id"]

    asyncio.run(scenario())


def test_escalation_respects_chat_settings():
    async def scenario():
        service, _, tg = _setup()
        settings = ModSettings.from_chat(
            {"mod": {"warn_limit": 1, "warn_action": "ban", "warn_action_seconds": None}}
        )
        outcome = await service.warn(tg, CHAT, TARGET, ADMIN, settings)
        assert outcome.escalated.kind == "ban"
        assert tg.calls == [("ban", 5, None)]

    asyncio.run(scenario())


def test_failed_escalation_keeps_warns_for_retry():
    async def scenario():
        service, repo, tg = _setup()
        settings = ModSettings(warn_limit=1)
        tg.fail = ModerationError("bot_no_rights")
        with pytest.raises(ModerationError):
            await service.warn(tg, CHAT, TARGET, ADMIN, settings)
        assert await repo.count_warns(BOT_ID, CHAT, 5) == 1

    asyncio.run(scenario())


def test_warn_ttl_sets_expiry():
    async def scenario():
        service, repo, tg = _setup()
        settings = ModSettings(warn_ttl_seconds=3600)
        await service.warn(tg, CHAT, TARGET, ADMIN, settings)
        assert repo.rows[0]["expires_at"] == NOW + timedelta(hours=1)

    asyncio.run(scenario())


def test_unwarn_and_empty_unwarn():
    async def scenario():
        service, repo, tg = _setup()
        await service.warn(tg, CHAT, TARGET, ADMIN, ModSettings())
        await service.unwarn(tg, CHAT, TARGET, ADMIN)
        assert await repo.count_warns(BOT_ID, CHAT, 5) == 0
        with pytest.raises(ModerationError) as caught:
            await service.unwarn(tg, CHAT, TARGET, ADMIN)
        assert caught.value.code == "no_warns"

    asyncio.run(scenario())


def test_lift_calls_telegram_and_closes_record():
    async def scenario():
        service, repo, tg = _setup()
        await service.punish(tg, CHAT, "mute", TARGET, ADMIN)
        tg.calls.clear()
        await service.lift(tg, CHAT, "mute", TARGET, ADMIN)
        assert tg.calls == [("unmute", 5)]
        assert repo.rows[0]["revoked_at"] is not None

    asyncio.run(scenario())


def test_undo_button_lifts_once():
    async def scenario():
        service, repo, tg = _setup()
        await service.punish(tg, CHAT, "ban", TARGET, ADMIN)
        tg.calls.clear()
        outcome = await service.undo(tg, CHAT, 1, ADMIN)
        assert outcome.kind == "ban" and tg.calls == [("unban", 5)]
        with pytest.raises(ModerationError) as caught:
            await service.undo(tg, CHAT, 1, ADMIN)
        assert caught.value.code == "already"

    asyncio.run(scenario())


def test_undo_expired_mute_does_not_touch_telegram():
    async def scenario():
        service, repo, tg = _setup()
        await service.punish(tg, CHAT, "mute", TARGET, ADMIN, seconds=60)
        repo.rows[0]["expires_at"] = NOW - timedelta(seconds=1)
        tg.calls.clear()
        with pytest.raises(ModerationError) as caught:
            await service.undo(tg, CHAT, 1, ADMIN)
        assert caught.value.code == "already" and tg.calls == []

    asyncio.run(scenario())


def test_undo_warn_and_kick():
    async def scenario():
        service, repo, tg = _setup()
        await service.warn(tg, CHAT, TARGET, ADMIN, ModSettings())
        tg.calls.clear()
        await service.undo(tg, CHAT, 1, ADMIN)
        assert tg.calls == []
        assert await repo.count_warns(BOT_ID, CHAT, 5) == 0

        await service.punish(tg, CHAT, "kick", TARGET, ADMIN)
        with pytest.raises(ModerationError) as caught:
            await service.undo(tg, CHAT, 2, ADMIN)
        assert caught.value.code == "kick_undo"

        with pytest.raises(ModerationError) as caught:
            await service.undo(tg, CHAT, 99, ADMIN)
        assert caught.value.code == "not_found"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "target_id,code",
    [(BOT_ID, "target_bot"), (ADMIN.id, "self"), (2, "target_admin")],
)
def test_check_target_refuses(target_id, code):
    async def scenario():
        rights = {ADMIN.id: OWNER_RIGHTS, 2: OWNER_RIGHTS, BOT_ID: OWNER_RIGHTS}
        service, _, _ = _setup(rights)
        with pytest.raises(ModerationError) as caught:
            await service.check_target(BOT, CHAT, ADMIN.id, target_id)
        assert caught.value.code == code

    asyncio.run(scenario())


def test_check_target_allows_regular_member():
    async def scenario():
        service, _, _ = _setup()
        await service.check_target(BOT, CHAT, ADMIN.id, TARGET.id)

    asyncio.run(scenario())


def test_check_bot_needs_restrict_right_and_rechecks_next_time():
    async def scenario():
        weak = AdminRights(False, False, True, False, False, False, False)
        rights = {ADMIN.id: OWNER_RIGHTS, BOT_ID: weak}
        service, _, _ = _setup(rights)
        with pytest.raises(ModerationError) as caught:
            await service.check_bot(BOT, CHAT)
        assert caught.value.code == "bot_no_rights"

        rights[BOT_ID] = OWNER_RIGHTS  # админ выдал право, и повторная попытка сработает сразу
        await service.check_bot(BOT, CHAT)

    asyncio.run(scenario())


def test_settings_ignore_unknown_keys():
    assert ModSettings.from_chat({"mod": {"warn_limit": 5, "новое": 1}}).warn_limit == 5
    assert ModSettings.from_chat(None).warn_limit == 3
    assert ModSettings.from_chat({}).delete_commands is True


# --- Карточки ---


def test_card_is_small_and_has_all_fields():
    outcome = Outcome(
        "mute", TARGET, ADMIN, "спам", 7200, NOW + timedelta(hours=2), 1
    )
    card = render_card(outcome, 5)
    lines = card.splitlines()
    assert len(lines) == 4
    assert "Мут" in lines[0] and 'href="tg://user?id=5"' in lines[0]
    assert "2 ч" in lines[1] and "05.11 19:00" in lines[1]  # 14:00 UTC это 19:00 в Алматы
    assert "спам" in lines[2]
    assert "Админ" in lines[3]


def test_card_without_reason_and_forever():
    card = render_card(Outcome("ban", TARGET, ADMIN), 5)
    assert "Навсегда" in card
    assert "📝" not in card


def test_card_escapes_user_text():
    evil = Person(5, "<b>Вася</b>")
    card = render_card(Outcome("kick", evil, ADMIN, "<script>"), 5)
    assert "&lt;b&gt;Вася" in card and "&lt;script&gt;" in card
    assert "<script>" not in card


def test_kick_card_has_no_term_and_no_undo():
    outcome = Outcome("kick", TARGET, ADMIN, punishment_id=1)
    assert "⏳" not in render_card(outcome, 5)
    assert undo_target(outcome) is None


def test_warn_card_with_escalation():
    escalated = Outcome("mute", TARGET, ADMIN, None, 86400, NOW + timedelta(days=1), 9)
    outcome = Outcome(
        "warn", TARGET, ADMIN, "флуд", punishment_id=3, warn_count=3, warn_limit=3,
        escalated=escalated,
    )
    card = render_card(outcome, 5)
    assert "3/3" in card and "Лимит" in card and "1 д" in card
    assert undo_target(outcome) == 9


def test_history_and_journal_rendering():
    row = {
        "kind": "mute", "user_id": 5, "reason": None, "created_at": NOW,
        "expires_at": NOW + timedelta(hours=2), "revoked_at": None, "admin_name": None,
        "target_name": "<Вася>",
    }
    history = render_history(TARGET, [row], 5)
    assert "2 ч" in history and "без причины" in history and "бот" in history
    journal = render_journal([{**row, "revoked_at": NOW, "kind": "kick"}], 5)
    assert "&lt;Вася&gt;" in journal and "↩️" in journal and "разово" in journal
    assert "Записей нет" in render_journal([], 5)
