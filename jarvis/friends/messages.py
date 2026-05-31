# === F-FRIENDS [F3] · feature/friends-section · the maintainer-2026-05-01 ===
"""DirectMessageStore: local persistence for direct messages.

Branch-portable: writes to the same ``friends.db`` as :class:`FriendRegistry`
(no ``board_backend`` sub-project required). A future federation delivery
(Phase F5) can live alongside this as an adapter and reuse the same store
for local history display.

The class does NOT open its own connection — it accepts the open
``aiosqlite.Connection`` from :class:`FriendRegistry` and shares
transactions, WAL mode, and lifecycle with it.
"""
from __future__ import annotations

import time
from typing import Literal
from uuid import UUID, uuid4

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from .models import FriendChannelKind


def _now_ns() -> int:
    return time.time_ns()


class DirectMessage(BaseModel):
    """A single direct message, inbound or outbound.

    ``delivered`` is always ``True`` in local-only mode (F3); the field
    exists for future federation phases in which outbound messages are only
    confirmed after a successful push to a remote Jarvis instance.
    """

    model_config = ConfigDict(frozen=False)

    id: UUID = Field(default_factory=uuid4)
    friend_id: UUID
    direction: Literal["inbound", "outbound"]
    text: str = Field(..., min_length=1, max_length=8192)
    channel: FriendChannelKind
    created_at_ns: int = Field(default_factory=_now_ns)
    delivered: bool = True


class DirectMessageStore:
    """Async SQLite store for direct messages.

    Shares the connection with :class:`FriendRegistry`; the
    ``direct_messages`` table is created alongside the registry schema via
    ``CREATE TABLE IF NOT EXISTS`` in ``SCHEMA_SQL``.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(self, message: DirectMessage) -> DirectMessage:
        await self._conn.execute(
            "INSERT INTO direct_messages "
            "(id, friend_id, direction, text, channel, created_at_ns, delivered) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(message.id),
                str(message.friend_id),
                message.direction,
                message.text,
                message.channel,
                message.created_at_ns,
                int(message.delivered),
            ),
        )
        return message

    async def list_for_friend(
        self, friend_id: UUID, *, limit: int = 200
    ) -> list[DirectMessage]:
        """Returns messages in chronological ascending order (newest last), capped at ``limit``.

        We read the most recent ``limit`` rows using DESC + LIMIT and then
        reverse the list for the UI — this keeps the index access efficient
        while the output is in history-friendly order.
        """
        async with self._conn.execute(
            "SELECT id, friend_id, direction, text, channel, created_at_ns, delivered "
            "FROM direct_messages WHERE friend_id = ? "
            "ORDER BY created_at_ns DESC LIMIT ?",
            (str(friend_id), int(limit)),
        ) as cur:
            rows = await cur.fetchall()
        rows = list(reversed(rows))
        return [
            DirectMessage(
                id=UUID(r["id"]),
                friend_id=UUID(r["friend_id"]),
                direction=r["direction"],
                text=r["text"],
                channel=r["channel"],
                created_at_ns=r["created_at_ns"],
                delivered=bool(r["delivered"]),
            )
            for r in rows
        ]

    async def delete_for_friend(self, friend_id: UUID) -> None:
        await self._conn.execute(
            "DELETE FROM direct_messages WHERE friend_id = ?",
            (str(friend_id),),
        )
