"""Personal Jarvis Socials — the friend-graph service.

Not to be confused with :mod:`jarvis.ui.web.socials_routes`, which is CRUD for
the *project's* marketing links (``/api/socials``). This module is the people
side: friends who also run Personal Jarvis, what they let you see of their
usage, and writing to them. Its HTTP surface lives under ``/api/friends``.

What this service owns
----------------------
Everything that has to hold together across the registry, the peer transport,
the usage pulse, and the local profile. Routes and CLI commands call it; they
do not re-implement the ordering rules, because those rules are where the
mistakes are:

- **A message is stored before it is sent.** Delivery failure marks a row
  undelivered; it never discards what the user typed.
- **Peer link beats channel.** A friend reachable both directly and by Telegram
  gets the direct path — it is the one that stays inside the two machines.
- **Inbound authorisation comes from the signature, never from the body.** A
  request names a node; that node is looked up in the local link table; an
  unknown one is refused before anything is stored.
- **Two gates before a number leaves.** The master ``share_pulse`` switch, then
  the per-friend profile. Either one saying no is a no.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID, uuid4

from .identity import InviteError, SocialIdentity, decode_invite
from .messages import DirectMessage
from .models import Friend
from .peer import PeerLink, PeerTransport, PeerUnreachable
from .profile import SocialProfile, SocialProfileStore
from .pulse import UsagePulse, build_local_pulse, redact_pulse
from .registry import FriendNotFoundError, FriendRegistry

log = logging.getLogger(__name__)

#: Cap on a single direct message. Matches the REST body limit so the two
#: cannot drift into "the API accepted it, the store rejected it".
MAX_MESSAGE_CHARS = 4096


class SocialsError(RuntimeError):
    """A social action could not be completed, with a message for the user."""


class PeerRejected(SocialsError):
    """An inbound peer request was not authorised. Always answered as 403."""


class SocialsService:
    """The one place the social features are composed.

    Every dependency is injectable so tests can run the whole flow — invite,
    accept, send, receive, pulse — against two in-memory registries with no
    network at all.
    """

    def __init__(
        self,
        registry: FriendRegistry,
        *,
        identity: SocialIdentity | None = None,
        profiles: SocialProfileStore | None = None,
        ledger: Any | None = None,
        transport: PeerTransport | None = None,
    ) -> None:
        self._registry = registry
        self._identity = identity or SocialIdentity.load_or_create()
        self._profiles = profiles or SocialProfileStore()
        self._ledger = ledger
        self._transport = transport or PeerTransport(self._identity)

    # ------------------------------------------------------------------
    # This instance
    # ------------------------------------------------------------------

    @property
    def identity(self) -> SocialIdentity:
        return self._identity

    @property
    def profiles(self) -> SocialProfileStore:
        return self._profiles

    def profile(self) -> SocialProfile:
        return self._profiles.load()

    def update_profile(self, **changes: Any) -> SocialProfile:
        return self._profiles.update(**changes)

    def local_pulse(self) -> UsagePulse:
        """This instance's own, unredacted pulse — for the owner's own eyes."""
        pulse, _ = build_local_pulse(ledger=self._ledger)
        return pulse

    def me(self) -> dict[str, Any]:
        """Identity + profile + own pulse, as the "this is you" card shows it."""
        profile = self.profile()
        pulse, sources = build_local_pulse(ledger=self._ledger)
        return {
            "node_id": self._identity.node_id,
            "short_id": self._identity.short_id,
            "display_name": profile.resolved_display_name(),
            "tagline": profile.tagline,
            "endpoint": profile.endpoint,
            "share_pulse": profile.share_pulse,
            "accept_invites": profile.accept_invites,
            "reachable": bool(profile.endpoint),
            "pulse": pulse.model_dump(),
            "sources": sources.model_dump(),
        }

    # ------------------------------------------------------------------
    # Invites
    # ------------------------------------------------------------------

    def create_invite(self, *, endpoint: str | None = None) -> dict[str, Any]:
        """Mint an invite code for this instance.

        ``endpoint`` overrides the stored one for this code only — useful when
        someone is on a LAN today and a tunnel tomorrow and does not want that
        choice written into their profile.
        """
        profile = self.profile()
        address = endpoint if endpoint is not None else profile.endpoint
        code = self._identity.create_invite(
            display_name=profile.resolved_display_name(), endpoint=address
        )
        return {
            "code": code,
            "node_id": self._identity.node_id,
            "short_id": self._identity.short_id,
            "display_name": profile.resolved_display_name(),
            "endpoint": address,
            # Said plainly rather than implied: a code with no address is still
            # useful (the other side can be the reachable one), but only one of
            # the two directions will work, and the user has to know which.
            "reachable": bool(address),
        }

    async def accept_invite(
        self,
        code: str,
        *,
        note: str | None = None,
        display_name: str | None = None,
        greet: bool = True,
    ) -> dict[str, Any]:
        """Turn an invite code into a friend with a peer link.

        Raises :class:`SocialsError` for a code that does not verify, names this
        very instance, or belongs to a node that is already a friend — all three
        are things a person can act on, so they get their own message.
        """
        try:
            invite = decode_invite(code)
        except InviteError as exc:
            raise SocialsError(str(exc)) from exc

        if invite.node_id == self._identity.node_id:
            raise SocialsError("That is your own invite code.")

        existing = await self._registry.peers.get_by_node(invite.node_id)
        if existing is not None:
            try:
                friend = await self._registry.get_friend(existing.friend_id)
                name = friend.display_name
            except FriendNotFoundError:
                name = invite.display_name or "this node"
            raise SocialsError(f"You are already connected with {name}.")

        friend = Friend(
            display_name=(display_name or invite.display_name or "Jarvis friend")[:120],
            note=note,
        )
        await self._registry.add_friend(friend)
        link = PeerLink(
            friend_id=friend.id,
            node_id=invite.node_id,
            endpoint=invite.endpoint,
            peer_display_name=invite.display_name or None,
            state="pending",
        )
        await self._registry.peers.upsert(link)

        greeting: dict[str, Any] = {"attempted": False, "ok": False, "error": None}
        if greet and link.can_send:
            greeting["attempted"] = True
            try:
                await self._say_hello(link)
                greeting["ok"] = True
            except PeerUnreachable as exc:
                # A pending link is a perfectly good outcome: the friend exists
                # locally, and the handshake completes the first time either
                # side manages to reach the other.
                greeting["error"] = str(exc)
                await self._registry.peers.mark_error(friend.id, str(exc))

        stored = await self._registry.peers.get(friend.id)
        return {
            "friend": friend,
            "link": stored or link,
            "greeting": greeting,
        }

    async def _say_hello(self, link: PeerLink) -> None:
        profile = self.profile()
        await self._transport.hello(
            link,
            display_name=profile.resolved_display_name(),
            endpoint=profile.endpoint,
        )
        await self._registry.peers.mark_seen(link.friend_id, state="active")

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    async def overview(self) -> list[dict[str, Any]]:
        """One row per friend with everything the social list renders.

        Built with three queries plus one per friend rather than the naive
        query-per-field: this endpoint is polled while the section is open.
        """
        friends = await self._registry.list_friends()
        unread = await self._registry.messages.unread_counts()
        links = {str(link.friend_id): link for link in await self._registry.peers.list_all()}

        rows: list[dict[str, Any]] = []
        for friend in friends:
            key = str(friend.id)
            link = links.get(key)
            channels = await self._registry.channels_for_friend(friend.id)
            last = await self._registry.messages.last_message(friend.id)
            permission = await self._registry.get_status_permission(friend.id)
            pulse = link.pulse() if link is not None else None
            rows.append(
                {
                    "id": key,
                    "display_name": friend.display_name,
                    "avatar_url": friend.avatar_url,
                    "note": friend.note,
                    "created_at_ns": friend.created_at_ns,
                    "channels": [c.model_dump(mode="json") for c in channels],
                    "permission_profile": permission.profile,
                    "unread": int(unread.get(key, 0)),
                    "last_message": _preview(last),
                    "peer": _link_summary(link),
                    "pulse": pulse,
                    "pulse_fresh": bool(
                        link is not None and link.pulse_is_fresh()
                    ),
                    "presence": _presence_of(link, pulse),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_message(
        self,
        friend_id: UUID,
        text: str,
        *,
        telegram_sender: Any | None = None,
    ) -> DirectMessage:
        """Write to a friend by the best route available.

        Returns the stored message. A peer that could not be reached yields a
        message with ``delivered=False`` rather than an exception — the text is
        kept and can be retried; only "there is no route at all" is an error.
        """
        body = (text or "").strip()
        if not body:
            raise SocialsError("An empty message is not sent.")
        if len(body) > MAX_MESSAGE_CHARS:
            raise SocialsError(
                f"That message is longer than {MAX_MESSAGE_CHARS} characters."
            )
        await self._registry.get_friend(friend_id)  # raises FriendNotFoundError

        link = await self._registry.peers.get(friend_id)
        if link is not None and link.state == "blocked":
            raise SocialsError("This friend is blocked.")

        if link is not None and link.can_send:
            return await self._send_via_peer(link, body)

        channels = await self._registry.channels_for_friend(friend_id)
        primary = next(
            (c for c in channels if c.is_primary), channels[0] if channels else None
        )
        if primary is not None and primary.channel == "telegram" and telegram_sender is not None:
            await telegram_sender(primary.handle, body)
            return await self._registry.messages.add(
                DirectMessage(
                    friend_id=friend_id,
                    direction="outbound",
                    text=body,
                    channel="telegram",
                    delivered=True,
                )
            )

        if primary is not None and primary.channel == "jarvis_pubkey":
            # A public key alone is an identity, not an address: there is
            # nowhere to send it. The message is kept and marked undelivered so
            # the thread stays honest — it becomes deliverable the moment the
            # friend supplies an invite code carrying an address.
            return await self._registry.messages.add(
                DirectMessage(
                    friend_id=friend_id,
                    direction="outbound",
                    text=body,
                    channel="jarvis_pubkey",
                    delivered=False,
                )
            )

        if link is not None:
            raise SocialsError(
                "No address on file for this friend — ask them for an invite "
                "code that contains one, or link a Telegram chat."
            )
        if primary is not None and primary.channel == "telegram":
            raise SocialsError(
                "Telegram is not running, so this message cannot be sent."
            )
        raise SocialsError(
            "This friend has no way to be reached yet. Accept their invite code "
            "or link a channel first."
        )

    async def _send_via_peer(self, link: PeerLink, text: str) -> DirectMessage:
        message = DirectMessage(
            friend_id=link.friend_id,
            direction="outbound",
            text=text,
            channel="jarvis_link",
            delivered=False,
        )
        await self._registry.messages.add(message)
        try:
            await self._transport.deliver(
                link,
                message_id=str(message.id),
                text=text,
                created_at_ns=message.created_at_ns,
            )
        except PeerUnreachable as exc:
            await self._registry.peers.mark_error(link.friend_id, str(exc))
            return message
        await self._registry.messages.mark_delivered(message.id)
        await self._registry.peers.mark_seen(link.friend_id, state="active")
        message.delivered = True
        return message

    async def retry_pending(self, friend_id: UUID) -> dict[str, int]:
        """Re-attempt every undelivered outbound message of one thread."""
        link = await self._registry.peers.get(friend_id)
        pending = await self._registry.messages.pending_outbound(friend_id)
        if link is None or not link.can_send:
            return {"pending": len(pending), "delivered": 0}
        delivered = 0
        for message in pending:
            try:
                await self._transport.deliver(
                    link,
                    message_id=str(message.id),
                    text=message.text,
                    created_at_ns=message.created_at_ns,
                )
            except PeerUnreachable as exc:
                await self._registry.peers.mark_error(friend_id, str(exc))
                break  # the next one will fail the same way
            await self._registry.messages.mark_delivered(message.id)
            delivered += 1
        if delivered:
            await self._registry.peers.mark_seen(friend_id, state="active")
        return {"pending": len(pending) - delivered, "delivered": delivered}

    async def mark_read(self, friend_id: UUID) -> int:
        return await self._registry.messages.mark_read(friend_id)

    # ------------------------------------------------------------------
    # Pulse exchange
    # ------------------------------------------------------------------

    async def refresh_pulse(self, friend_id: UUID) -> dict[str, Any]:
        """Fetch a friend's pulse now and cache it on the link."""
        link = await self._registry.peers.get(friend_id)
        if link is None:
            raise SocialsError(
                "This friend has no direct link — usage sharing needs one."
            )
        if not link.can_send:
            raise SocialsError(
                "No address on file for this friend, so their usage cannot be fetched."
            )
        try:
            answer = await self._transport.fetch_pulse(link)
        except PeerUnreachable as exc:
            await self._registry.peers.mark_error(friend_id, str(exc))
            raise SocialsError(str(exc)) from exc

        if not answer.get("shared", True):
            # An explicit "I share nothing" is information, not an error: cache
            # it so the card can say so instead of showing a spinner forever.
            payload = {"shared": False, "profile": answer.get("profile", "minimal")}
            await self._registry.peers.store_pulse(friend_id, payload)
            return payload

        raw = answer.get("pulse")
        if not isinstance(raw, dict):
            raise SocialsError("The other instance sent an unreadable usage card.")
        raw["fetched_at_ns"] = time.time_ns()
        raw["shared"] = True
        await self._registry.peers.store_pulse(friend_id, raw)
        await self._registry.peers.mark_seen(friend_id, state="active")
        return raw

    async def refresh_all_pulses(self) -> dict[str, int]:
        """Best-effort refresh across every linked friend."""
        links = await self._registry.peers.list_all()
        ok = 0
        failed = 0
        for link in links:
            if not link.can_send:
                continue
            try:
                await self.refresh_pulse(link.friend_id)
                ok += 1
            except SocialsError:
                failed += 1
        return {"refreshed": ok, "failed": failed}

    # ------------------------------------------------------------------
    # Inbound (called by the peer routes, after signature verification)
    # ------------------------------------------------------------------

    async def resolve_peer(self, node_id: str) -> tuple[Friend, PeerLink]:
        """Map a verified node id onto a friend, or refuse.

        This is the authorisation boundary. A node that is not in the link
        table, or one whose friend row has vanished, gets nothing — the caller
        turns :class:`PeerRejected` into a 403 with no detail about which of the
        two it was.
        """
        link = await self._registry.peers.get_by_node(node_id)
        if link is None or link.state == "blocked":
            raise PeerRejected("Unknown node.")
        try:
            friend = await self._registry.get_friend(link.friend_id)
        except FriendNotFoundError as exc:
            raise PeerRejected("Unknown node.") from exc
        return friend, link

    async def handle_hello(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Complete a handshake initiated by the other side.

        Two cases. A node already on file is confirming the link — mark it
        active and answer. An unknown node is a stranger: accepted only when
        ``accept_invites`` is on, and then only as a *pending* friend the user
        still sees and can delete. Nothing here is silent.
        """
        profile = self.profile()
        peer_name = str(body.get("display_name") or "").strip()[:120] or "Jarvis friend"
        peer_endpoint = _clean(body.get("endpoint"))

        link = await self._registry.peers.get_by_node(node_id)
        if link is not None:
            if link.state == "blocked":
                raise PeerRejected("Unknown node.")
            link.peer_display_name = peer_name
            if peer_endpoint:
                link.endpoint = peer_endpoint
            link.state = "active"
            link.last_seen_ns = time.time_ns()
            link.last_error = None
            await self._registry.peers.upsert(link)
            return self._hello_answer(profile, known=True)

        if not profile.accept_invites:
            raise PeerRejected("Not accepting new links.")

        friend = Friend(
            display_name=peer_name,
            note="Reached out to you — accepted automatically.",
        )
        await self._registry.add_friend(friend)
        await self._registry.peers.upsert(
            PeerLink(
                friend_id=friend.id,
                node_id=node_id,
                endpoint=peer_endpoint,
                peer_display_name=peer_name,
                state="active",
                last_seen_ns=time.time_ns(),
            )
        )
        log.info("socials: accepted a new peer link from %s", node_id[:12])
        return self._hello_answer(profile, known=False)

    def _hello_answer(self, profile: SocialProfile, *, known: bool) -> dict[str, Any]:
        return {
            "ok": True,
            "known": known,
            "node_id": self._identity.node_id,
            "display_name": profile.resolved_display_name(),
            "endpoint": profile.endpoint,
        }

    async def handle_inbox(self, node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Store one inbound direct message from a verified peer."""
        _, link = await self.resolve_peer(node_id)
        text = str(body.get("text") or "").strip()
        if not text:
            raise SocialsError("Empty message.")
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[:MAX_MESSAGE_CHARS]
        remote_id = str(body.get("message_id") or "") or str(uuid4())
        created = _as_int(body.get("created_at_ns")) or time.time_ns()

        stored = await self._registry.messages.add_once(
            DirectMessage(
                friend_id=link.friend_id,
                direction="inbound",
                text=text,
                channel="jarvis_link",
                created_at_ns=created,
                delivered=True,
                remote_id=remote_id,
            )
        )
        await self._registry.peers.mark_seen(link.friend_id, state="active")
        # A duplicate answers "accepted" too: the sender retried because it did
        # not hear back, and a 409 would only make it retry again.
        return {"ok": True, "duplicate": stored is None}

    async def handle_pulse_request(self, node_id: str) -> dict[str, Any]:
        """Answer a verified peer with the pulse they are allowed to see."""
        _, link = await self.resolve_peer(node_id)
        profile = self.profile()
        permission = await self._registry.get_status_permission(link.friend_id)
        if not profile.share_pulse:
            return {"shared": False, "profile": permission.profile}
        full, _ = build_local_pulse(ledger=self._ledger)
        shared = redact_pulse(full, permission.profile, permission.custom_whitelist)
        await self._registry.peers.mark_seen(link.friend_id, state="active")
        return {"shared": True, "pulse": shared.model_dump(exclude={"fetched_at_ns"})}

    # ------------------------------------------------------------------
    # Link management
    # ------------------------------------------------------------------

    async def set_link_state(self, friend_id: UUID, state: str) -> PeerLink:
        link = await self._registry.peers.get(friend_id)
        if link is None:
            raise SocialsError("This friend has no direct link.")
        if state not in {"pending", "active", "blocked"}:
            raise SocialsError(f"Unknown link state {state!r}.")
        link.state = state  # type: ignore[assignment]
        await self._registry.peers.upsert(link)
        return link

    async def set_link_endpoint(self, friend_id: UUID, endpoint: str | None) -> PeerLink:
        """Point an existing link at a new address (a friend moved networks)."""
        link = await self._registry.peers.get(friend_id)
        if link is None:
            raise SocialsError("This friend has no direct link.")
        link.endpoint = _clean(endpoint)
        link.last_error = None
        await self._registry.peers.upsert(link)
        return link


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _preview(message: DirectMessage | None) -> dict[str, Any] | None:
    """A one-line thread preview. Truncated here, not in the UI, so the API
    never ships a whole conversation to render forty pixels of text."""
    if message is None:
        return None
    text = message.text
    return {
        "direction": message.direction,
        "text": text[:140] + ("…" if len(text) > 140 else ""),
        "timestamp_ns": message.created_at_ns,
        "delivered": message.delivered,
    }


def _link_summary(link: PeerLink | None) -> dict[str, Any] | None:
    if link is None:
        return None
    return {
        "node_id": link.node_id,
        "short_id": link.short_id,
        "endpoint": link.endpoint,
        "state": link.state,
        "last_seen_ns": link.last_seen_ns,
        "last_error": link.last_error,
        "can_send": link.can_send,
    }


def _presence_of(link: PeerLink | None, pulse: dict[str, Any] | None) -> str:
    """What the dot next to a friend's name means.

    Presence is the friend's *own* statement about themselves (their pulse
    bucket), not our observation of them. When they share no bucket we fall
    back to "when did this link last work", which is honest about being a
    connection fact rather than a claim about the person.
    """
    if isinstance(pulse, dict):
        bucket = pulse.get("last_active_bucket")
        if isinstance(bucket, str) and bucket != "unknown":
            return bucket
    if link is None:
        return "unknown"
    if link.state == "blocked":
        return "blocked"
    if link.last_seen_ns <= 0:
        return "never"
    from .pulse import last_active_bucket as _bucket

    return _bucket(link.last_seen_ns)


def _clean(value: Any) -> str | None:
    from .identity import _clean_endpoint

    return _clean_endpoint(value)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "MAX_MESSAGE_CHARS",
    "PeerRejected",
    "SocialsError",
    "SocialsService",
]
