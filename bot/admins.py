"""Права администраторов чата с кэшем, чтобы не спрашивать Telegram на каждую команду."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, NamedTuple

from .cache import TTLCache


class AdminRights(NamedTuple):
    is_owner: bool
    can_restrict: bool
    can_delete: bool
    can_pin: bool
    can_promote: bool
    can_change_info: bool
    can_invite: bool


OWNER_RIGHTS = AdminRights(True, True, True, True, True, True, True)


def rights_from_member(member: Any) -> AdminRights:
    if member.status == "creator":
        return OWNER_RIGHTS
    return AdminRights(
        is_owner=False,
        can_restrict=bool(getattr(member, "can_restrict_members", False)),
        can_delete=bool(getattr(member, "can_delete_messages", False)),
        can_pin=bool(getattr(member, "can_pin_messages", False)),
        can_promote=bool(getattr(member, "can_promote_members", False)),
        can_change_info=bool(getattr(member, "can_change_info", False)),
        can_invite=bool(getattr(member, "can_invite_users", False)),
    )


def rights_map(members: Iterable[Any]) -> dict[int, AdminRights]:
    return {m.user.id: rights_from_member(m) for m in members}


Fetcher = Callable[[Any, int], Awaitable[Iterable[Any]]]


class AdminCache:
    def __init__(self, fetch: Fetcher, *, cache_size: int = 5_000, ttl: float = 600.0) -> None:
        self._fetch = fetch
        self.cache: TTLCache[tuple[int, int], dict[int, AdminRights]] = TTLCache(cache_size, ttl)

    async def get_all(self, bot: Any, chat_id: int) -> dict[int, AdminRights]:
        async def load() -> dict[int, AdminRights]:
            return rights_map(await self._fetch(bot, chat_id))

        return await self.cache.get_or_load((bot.id, chat_id), load)

    async def get(self, bot: Any, chat_id: int, user_id: int) -> AdminRights | None:
        return (await self.get_all(bot, chat_id)).get(user_id)

    def invalidate(self, bot_id: int, chat_id: int) -> None:
        self.cache.delete((bot_id, chat_id))
