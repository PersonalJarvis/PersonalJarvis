# === F-FRIENDS [F0] · feature/friends-section · ruben-2026-04-30 ===
"""Pydantic data types for the Friends layer.

Three related models:

- :class:`Friend` — the person (UUID, display name, avatar, note).
- :class:`FriendChannel` — a concrete channel link (Telegram chat ID
  or Jarvis public key). A friend can have multiple channels.
- :class:`FriendStatusPermission` — per-friend profile for live status sharing.

``StatusProfile`` is a Literal — the concrete whitelists per profile live
as code in :mod:`jarvis.friends.status_filter` (Phase F4), not in the DB.
Rationale: constraint-self-bypass protection (Phase-7 plan AP-1/AP-11).
"""
from __future__ import annotations

import time
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

FriendChannelKind = Literal["telegram", "jarvis_pubkey"]
"""Which transport belongs to this channel link."""

StatusProfile = Literal["minimal", "standard", "detailed"]
"""Sharing profile for live status (see plan F4)."""


def _now_ns() -> int:
    return time.time_ns()


class Friend(BaseModel):
    """A person in the friends list, identified independently of any channel."""

    model_config = ConfigDict(frozen=False)

    id: UUID = Field(default_factory=uuid4)
    display_name: str = Field(..., min_length=1, max_length=120)
    avatar_url: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    created_at_ns: int = Field(default_factory=_now_ns)


class FriendChannel(BaseModel):
    """Link between a friend and a concrete channel.

    Composite key: ``(friend_id, channel, handle)``. ``is_primary`` marks
    the preferred channel for outbound messages when a friend has multiple
    channels. Exactly one primary per friend.
    """

    model_config = ConfigDict(frozen=False)

    friend_id: UUID
    channel: FriendChannelKind
    handle: str = Field(..., min_length=1, max_length=200)
    is_primary: bool = False
    linked_at_ns: int = Field(default_factory=_now_ns)


class FriendStatusPermission(BaseModel):
    """Per-friend permission for live status sharing."""

    model_config = ConfigDict(frozen=False)

    friend_id: UUID
    profile: StatusProfile = "minimal"
    custom_whitelist: list[str] | None = None
    updated_at_ns: int = Field(default_factory=_now_ns)
