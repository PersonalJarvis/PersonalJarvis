"""Peer links — the direct, server-less connection between two Jarvis instances.

The honest shape of this
------------------------
Personal Jarvis is a local application. Two friends running it are two laptops,
usually both behind NAT, with no shared account and no company in the middle.
There is therefore no way to make "just message my friend" work everywhere, and
pretending otherwise would produce a chat window that silently drops messages.

So a peer link is explicit about what it needs: **one side has to be reachable
at an address the other can open** — a LAN address, a Tailscale/VPN name, a
tunnel, or a published host. When that is true, everything works directly
between the two machines and no third party sees anything. When it is not,
delivery fails loudly, the message stays queued as undelivered, and the UI says
which side is unreachable. That failure is a fact about home networks, not a
bug, and the product states it rather than hiding it.

What travels
------------
Every request is a signed envelope (:mod:`jarvis.friends.identity`). The
receiver looks the sender's node id up in *its own* link table: an unknown key
is rejected before any handler runs, so an exposed port is not an open inbox.
Nothing here trusts a display name, an endpoint, or an id supplied in a body —
those are hints shown to the user, while authorisation always comes from the
signature.

Delivery is at-most-once per message id. The receiver stores the sender's
message id and rejects a repeat, so a retry after a timeout cannot duplicate a
message in the thread.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal
from uuid import UUID

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

PeerLinkState = Literal["pending", "active", "blocked"]
"""``pending`` — invited, never spoken. ``active`` — a signed round-trip
succeeded. ``blocked`` — the user cut them off; nothing in or out."""

#: Peer HTTP timeout. A friend's laptop is either awake and close or it is not;
#: a long timeout only makes the UI feel broken.
PEER_TIMEOUT_S = 8.0

#: How long a fetched pulse is considered current before the UI labels it stale.
PULSE_FRESH_NS = 15 * 60 * 1_000_000_000


class PeerError(RuntimeError):
    """Base class for peer transport failures."""


class PeerUnreachable(PeerError):
    """The peer could not be contacted, or answered with an error.

    Carries a message written for the user, because this is the error the chat
    window shows when a friend's machine is asleep.
    """


class PeerLink(BaseModel):
    """The direct connection to one friend's Jarvis instance."""

    model_config = ConfigDict(frozen=False)

    friend_id: UUID
    node_id: str = Field(..., min_length=16, max_length=128)
    endpoint: str | None = None
    peer_display_name: str | None = None
    state: PeerLinkState = "pending"
    created_at_ns: int = Field(default_factory=lambda: time.time_ns())
    last_seen_ns: int = 0
    last_error: str | None = None
    pulse_json: str | None = None
    pulse_at_ns: int = 0

    @property
    def can_send(self) -> bool:
        """Outbound is possible only with an address and without a block."""
        return self.state != "blocked" and bool(self.endpoint)

    @property
    def short_id(self) -> str:
        return "-".join(self.node_id[i : i + 4] for i in range(0, 12, 4))

    def pulse(self) -> dict[str, Any] | None:
        """The last fetched pulse, or ``None`` when there is none or it is unreadable."""
        if not self.pulse_json:
            return None
        try:
            value = json.loads(self.pulse_json)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    def pulse_is_fresh(self, *, now_ns: int | None = None) -> bool:
        if self.pulse_at_ns <= 0:
            return False
        now = int(now_ns if now_ns is not None else time.time_ns())
        return (now - self.pulse_at_ns) <= PULSE_FRESH_NS


class PeerLinkStore:
    """SQLite store for peer links, sharing the FriendRegistry connection.

    Like :class:`jarvis.friends.messages.DirectMessageStore`, this opens no
    connection of its own — the registry owns the lifecycle, the WAL mode, and
    the transaction boundary.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert(self, link: PeerLink) -> PeerLink:
        await self._conn.execute(
            "INSERT INTO friend_peer_links "
            "(friend_id, node_id, endpoint, peer_display_name, state, "
            " created_at_ns, last_seen_ns, last_error, pulse_json, pulse_at_ns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(friend_id) DO UPDATE SET "
            "node_id = excluded.node_id, "
            "endpoint = excluded.endpoint, "
            "peer_display_name = excluded.peer_display_name, "
            "state = excluded.state, "
            "last_seen_ns = excluded.last_seen_ns, "
            "last_error = excluded.last_error, "
            "pulse_json = excluded.pulse_json, "
            "pulse_at_ns = excluded.pulse_at_ns",
            (
                str(link.friend_id),
                link.node_id,
                link.endpoint,
                link.peer_display_name,
                link.state,
                link.created_at_ns,
                link.last_seen_ns,
                link.last_error,
                link.pulse_json,
                link.pulse_at_ns,
            ),
        )
        return link

    async def get(self, friend_id: UUID) -> PeerLink | None:
        async with self._conn.execute(
            "SELECT friend_id, node_id, endpoint, peer_display_name, state, "
            "created_at_ns, last_seen_ns, last_error, pulse_json, pulse_at_ns "
            "FROM friend_peer_links WHERE friend_id = ?",
            (str(friend_id),),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_link(row) if row is not None else None

    async def get_by_node(self, node_id: str) -> PeerLink | None:
        async with self._conn.execute(
            "SELECT friend_id, node_id, endpoint, peer_display_name, state, "
            "created_at_ns, last_seen_ns, last_error, pulse_json, pulse_at_ns "
            "FROM friend_peer_links WHERE node_id = ?",
            (str(node_id),),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_link(row) if row is not None else None

    async def list_all(self) -> list[PeerLink]:
        async with self._conn.execute(
            "SELECT friend_id, node_id, endpoint, peer_display_name, state, "
            "created_at_ns, last_seen_ns, last_error, pulse_json, pulse_at_ns "
            "FROM friend_peer_links ORDER BY created_at_ns ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_link(r) for r in rows]

    async def delete(self, friend_id: UUID) -> None:
        await self._conn.execute(
            "DELETE FROM friend_peer_links WHERE friend_id = ?", (str(friend_id),)
        )

    async def mark_seen(
        self,
        friend_id: UUID,
        *,
        now_ns: int | None = None,
        state: PeerLinkState | None = None,
    ) -> None:
        """Record a successful exchange; optionally promote the link's state."""
        stamp = int(now_ns if now_ns is not None else time.time_ns())
        if state is None:
            await self._conn.execute(
                "UPDATE friend_peer_links SET last_seen_ns = ?, last_error = NULL "
                "WHERE friend_id = ?",
                (stamp, str(friend_id)),
            )
            return
        await self._conn.execute(
            "UPDATE friend_peer_links SET last_seen_ns = ?, last_error = NULL, "
            "state = ? WHERE friend_id = ?",
            (stamp, state, str(friend_id)),
        )

    async def mark_error(self, friend_id: UUID, message: str) -> None:
        await self._conn.execute(
            "UPDATE friend_peer_links SET last_error = ? WHERE friend_id = ?",
            (str(message)[:500], str(friend_id)),
        )

    async def store_pulse(
        self, friend_id: UUID, pulse: dict[str, Any], *, now_ns: int | None = None
    ) -> None:
        stamp = int(now_ns if now_ns is not None else time.time_ns())
        await self._conn.execute(
            "UPDATE friend_peer_links SET pulse_json = ?, pulse_at_ns = ?, "
            "last_seen_ns = ?, last_error = NULL WHERE friend_id = ?",
            (json.dumps(pulse, ensure_ascii=False), stamp, stamp, str(friend_id)),
        )


class PeerTransport:
    """Signed HTTP calls to a friend's instance.

    The client is created per call rather than held open: peer traffic is a
    handful of requests a day, and a long-lived pool to a laptop that is asleep
    most of the time buys nothing and complicates shutdown.
    """

    def __init__(
        self,
        identity: Any,
        *,
        timeout_s: float = PEER_TIMEOUT_S,
        client_factory: Any | None = None,
    ) -> None:
        self._identity = identity
        self._timeout_s = float(timeout_s)
        # Tests inject a factory returning a stub client with the same surface;
        # production leaves it None and gets httpx.
        self._client_factory = client_factory

    async def hello(
        self,
        link: PeerLink,
        *,
        display_name: str,
        endpoint: str | None,
    ) -> dict[str, Any]:
        """Introduce this node to the peer and confirm the link both ways."""
        return await self._post(
            link,
            "/api/friends/peer/hello",
            {"display_name": display_name, "endpoint": endpoint},
        )

    async def deliver(
        self,
        link: PeerLink,
        *,
        message_id: str,
        text: str,
        created_at_ns: int,
    ) -> dict[str, Any]:
        """Push one direct message to the peer's inbox."""
        return await self._post(
            link,
            "/api/friends/peer/inbox",
            {
                "message_id": str(message_id),
                "text": str(text),
                "created_at_ns": int(created_at_ns),
            },
        )

    async def fetch_pulse(self, link: PeerLink) -> dict[str, Any]:
        """Ask the peer for the usage pulse *they* chose to share with us."""
        return await self._post(link, "/api/friends/peer/pulse", {})

    # -- internals -------------------------------------------------------

    async def _post(
        self, link: PeerLink, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if link.state == "blocked":
            raise PeerUnreachable("This friend is blocked.")
        if not link.endpoint:
            raise PeerUnreachable(
                "No address on file for this friend — ask them for an invite "
                "code that includes one."
            )
        envelope = self._identity.seal(body)
        url = f"{link.endpoint.rstrip('/')}{path}"
        try:
            client_cm = self._make_client()
            async with client_cm as client:
                response = await client.post(url, json=envelope)
        except Exception as exc:  # noqa: BLE001 — every transport failure is one story
            raise PeerUnreachable(
                f"Could not reach {link.endpoint} — is their Jarvis running and "
                f"reachable from here? ({type(exc).__name__})"
            ) from exc

        status = int(getattr(response, "status_code", 0))
        if status == 403:
            raise PeerUnreachable(
                "The other instance does not recognise this node — they need to "
                "accept your invite first."
            )
        if status >= 400:
            raise PeerUnreachable(f"The other instance answered with HTTP {status}.")
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return {}
        return payload if isinstance(payload, dict) else {}

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import httpx

        return httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=False)


def _row_to_link(row: aiosqlite.Row) -> PeerLink:
    return PeerLink(
        friend_id=UUID(row["friend_id"]),
        node_id=row["node_id"],
        endpoint=row["endpoint"],
        peer_display_name=row["peer_display_name"],
        state=row["state"],
        created_at_ns=row["created_at_ns"],
        last_seen_ns=row["last_seen_ns"] or 0,
        last_error=row["last_error"],
        pulse_json=row["pulse_json"],
        pulse_at_ns=row["pulse_at_ns"] or 0,
    )


__all__ = [
    "PEER_TIMEOUT_S",
    "PULSE_FRESH_NS",
    "PeerError",
    "PeerLink",
    "PeerLinkState",
    "PeerLinkStore",
    "PeerTransport",
    "PeerUnreachable",
]
