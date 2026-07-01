# === F-FRIENDS [F0] · feature/friends-section · alex-2026-04-30 ===
"""FriendRegistry: aiosqlite persistence for friends, channels, and permissions.

Lifecycle pattern adopted from :class:`jarvis.memory.recall.RecallStore`:
lazy-open, async context manager, WAL mode. One connection per process.

Schema (see :data:`SCHEMA_SQL`):

- ``friends``                    — the person (UUID PK).
- ``friend_channels``            — many-to-many via (friend_id, channel, handle); composite PK.
- ``friend_status_permissions``  — 1:1 to friend (friend_id PK).

We deliberately avoid foreign-key cascades (SQLite WAL + FK is subtle;
dependent rows are deleted manually in :meth:`delete_friend`). Migrations:
no Alembic; ``CREATE TABLE IF NOT EXISTS`` is sufficient for additive schema growth.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import aiosqlite

from .messages import DirectMessageStore
from .models import (
    Friend,
    FriendChannel,
    FriendChannelKind,
    FriendStatusPermission,
    StatusProfile,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS friends (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    avatar_url      TEXT,
    note            TEXT,
    created_at_ns   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS friend_channels (
    friend_id       TEXT NOT NULL,
    channel         TEXT NOT NULL,
    handle          TEXT NOT NULL,
    is_primary      INTEGER NOT NULL DEFAULT 0,
    linked_at_ns    INTEGER NOT NULL,
    PRIMARY KEY (friend_id, channel, handle)
);

CREATE INDEX IF NOT EXISTS idx_friend_channels_lookup
    ON friend_channels (channel, handle);

CREATE TABLE IF NOT EXISTS friend_status_permissions (
    friend_id           TEXT PRIMARY KEY,
    profile             TEXT NOT NULL DEFAULT 'minimal',
    custom_whitelist    TEXT,
    updated_at_ns       INTEGER NOT NULL
);

-- F3: Direct message persistence (local in friends.db, branch-portable).
-- Federation delivery will come later via a separate adapter.
CREATE TABLE IF NOT EXISTS direct_messages (
    id              TEXT PRIMARY KEY,
    friend_id       TEXT NOT NULL,
    direction       TEXT NOT NULL,
    text            TEXT NOT NULL,
    channel         TEXT NOT NULL,
    created_at_ns   INTEGER NOT NULL,
    delivered       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_dm_friend_time
    ON direct_messages (friend_id, created_at_ns);
"""


class FriendRegistryError(RuntimeError):
    """Base class for registry-specific errors."""


class FriendNotFoundError(FriendRegistryError):
    """The friend with the given ID does not exist."""


class FriendRegistry:
    """Async SQLite store for friends, their channels, and status permissions."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        if str(self._db_path) != ":memory:":
            await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA_SQL)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "FriendRegistry":
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise FriendRegistryError(
                "FriendRegistry not opened — use open() or 'async with'."
            )
        return self._conn

    # ------------------------------------------------------------------
    # Friend CRUD
    # ------------------------------------------------------------------

    async def add_friend(self, friend: Friend) -> Friend:
        conn = self._require_conn()
        await conn.execute(
            "INSERT INTO friends (id, display_name, avatar_url, note, created_at_ns) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(friend.id),
                friend.display_name,
                friend.avatar_url,
                friend.note,
                friend.created_at_ns,
            ),
        )
        permission = FriendStatusPermission(friend_id=friend.id)
        await self._upsert_permission(permission)
        return friend

    async def get_friend(self, friend_id: UUID) -> Friend:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, display_name, avatar_url, note, created_at_ns FROM friends WHERE id = ?",
            (str(friend_id),),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise FriendNotFoundError(f"Friend {friend_id} not found")
        return Friend(
            id=UUID(row["id"]),
            display_name=row["display_name"],
            avatar_url=row["avatar_url"],
            note=row["note"],
            created_at_ns=row["created_at_ns"],
        )

    async def list_friends(self) -> list[Friend]:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, display_name, avatar_url, note, created_at_ns "
            "FROM friends ORDER BY display_name COLLATE NOCASE"
        ) as cur:
            rows = await cur.fetchall()
        return [
            Friend(
                id=UUID(r["id"]),
                display_name=r["display_name"],
                avatar_url=r["avatar_url"],
                note=r["note"],
                created_at_ns=r["created_at_ns"],
            )
            for r in rows
        ]

    async def delete_friend(self, friend_id: UUID) -> None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT 1 FROM friends WHERE id = ?", (str(friend_id),)
        ) as cur:
            if await cur.fetchone() is None:
                raise FriendNotFoundError(f"Friend {friend_id} not found")
        await conn.execute(
            "DELETE FROM friend_channels WHERE friend_id = ?", (str(friend_id),)
        )
        await conn.execute(
            "DELETE FROM friend_status_permissions WHERE friend_id = ?",
            (str(friend_id),),
        )
        # F3: Cascade direct messages (no FK; manual cleanup like channels).
        await conn.execute(
            "DELETE FROM direct_messages WHERE friend_id = ?", (str(friend_id),)
        )
        await conn.execute("DELETE FROM friends WHERE id = ?", (str(friend_id),))

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def link_channel(self, link: FriendChannel) -> FriendChannel:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT 1 FROM friends WHERE id = ?", (str(link.friend_id),)
        ) as cur:
            if await cur.fetchone() is None:
                raise FriendNotFoundError(f"Friend {link.friend_id} not found")

        if link.is_primary:
            await conn.execute(
                "UPDATE friend_channels SET is_primary = 0 WHERE friend_id = ?",
                (str(link.friend_id),),
            )
        await conn.execute(
            "INSERT OR REPLACE INTO friend_channels "
            "(friend_id, channel, handle, is_primary, linked_at_ns) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(link.friend_id),
                link.channel,
                link.handle,
                int(link.is_primary),
                link.linked_at_ns,
            ),
        )
        return link

    async def unlink_channel(
        self, friend_id: UUID, channel: FriendChannelKind, handle: str
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            "DELETE FROM friend_channels "
            "WHERE friend_id = ? AND channel = ? AND handle = ?",
            (str(friend_id), channel, handle),
        )

    async def channels_for_friend(self, friend_id: UUID) -> list[FriendChannel]:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT friend_id, channel, handle, is_primary, linked_at_ns "
            "FROM friend_channels WHERE friend_id = ? "
            "ORDER BY is_primary DESC, linked_at_ns ASC",
            (str(friend_id),),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_channel(r) for r in rows]

    async def find_friend_by_channel(
        self, channel: FriendChannelKind, handle: str
    ) -> Friend | None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT f.id, f.display_name, f.avatar_url, f.note, f.created_at_ns "
            "FROM friends f INNER JOIN friend_channels c ON c.friend_id = f.id "
            "WHERE c.channel = ? AND c.handle = ? LIMIT 1",
            (channel, handle),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Friend(
            id=UUID(row["id"]),
            display_name=row["display_name"],
            avatar_url=row["avatar_url"],
            note=row["note"],
            created_at_ns=row["created_at_ns"],
        )

    # ------------------------------------------------------------------
    # Status Permissions
    # ------------------------------------------------------------------

    async def get_status_permission(self, friend_id: UUID) -> FriendStatusPermission:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT friend_id, profile, custom_whitelist, updated_at_ns "
            "FROM friend_status_permissions WHERE friend_id = ?",
            (str(friend_id),),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return FriendStatusPermission(friend_id=friend_id)
        custom = json.loads(row["custom_whitelist"]) if row["custom_whitelist"] else None
        return FriendStatusPermission(
            friend_id=UUID(row["friend_id"]),
            profile=row["profile"],
            custom_whitelist=custom,
            updated_at_ns=row["updated_at_ns"],
        )

    async def set_status_permission(
        self,
        friend_id: UUID,
        profile: StatusProfile,
        custom_whitelist: list[str] | None = None,
    ) -> FriendStatusPermission:
        permission = FriendStatusPermission(
            friend_id=friend_id, profile=profile, custom_whitelist=custom_whitelist
        )
        await self._upsert_permission(permission)
        return permission

    # ------------------------------------------------------------------
    # Direct-Messages (F3)
    # ------------------------------------------------------------------

    @property
    def messages(self) -> DirectMessageStore:
        """DirectMessageStore view that shares our connection.

        Returns a fresh store on every access — the store is stateless
        (no caches); the connection is the only shared resource.
        """
        return DirectMessageStore(self._require_conn())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _upsert_permission(self, permission: FriendStatusPermission) -> None:
        conn = self._require_conn()
        whitelist_json = (
            json.dumps(permission.custom_whitelist)
            if permission.custom_whitelist is not None
            else None
        )
        await conn.execute(
            "INSERT INTO friend_status_permissions "
            "(friend_id, profile, custom_whitelist, updated_at_ns) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(friend_id) DO UPDATE SET "
            "profile = excluded.profile, "
            "custom_whitelist = excluded.custom_whitelist, "
            "updated_at_ns = excluded.updated_at_ns",
            (
                str(permission.friend_id),
                permission.profile,
                whitelist_json,
                permission.updated_at_ns,
            ),
        )


def _row_to_channel(row: aiosqlite.Row) -> FriendChannel:
    return FriendChannel(
        friend_id=UUID(row["friend_id"]),
        channel=row["channel"],
        handle=row["handle"],
        is_primary=bool(row["is_primary"]),
        linked_at_ns=row["linked_at_ns"],
    )
