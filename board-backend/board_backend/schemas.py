"""Pydantic request/response models.

Security: ``SyncPayload`` **explicitly contains only** safe aggregate
fields. If a sync call sends fields with disallowed names
(``text``, ``transcript``, ``args``, ``output_preview`` …), it is
rejected by ``models_extra='forbid'``. This is the server-side mirror
of ``BoardAggregator.export_all_for_federation()`` (Plan §A-Smoke
"test_no_pii_in_aggregated_stats").
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

PubkeyHex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
SignatureHex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{128}$"),
]


class HealthResponse(BaseModel):
    ok: bool = True
    version: str
    schema_ok: bool = True


# ----------------------------------------------------------------------
# /api/v1/identity/register  (admin only)
# ----------------------------------------------------------------------

class RegisterRequest(BaseModel):
    pubkey: PubkeyHex
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]


class RegisterResponse(BaseModel):
    pubkey: PubkeyHex
    display_name: str
    created_at: datetime


# ----------------------------------------------------------------------
# /api/v1/sync  (signed)
# ----------------------------------------------------------------------

class DailyStatsItem(BaseModel):
    """A daily aggregation. Mirrors the BoardAggregator format from
    ``export_all_for_federation()`` — safe fields only.
    """
    model_config = ConfigDict(extra="forbid")

    date: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    tasks_completed: int = Field(ge=0)
    tasks_failed: int = Field(ge=0)
    tools_used: list[str] = Field(default_factory=list, max_length=200)
    unique_tools_count: int = Field(ge=0)
    voice_commands_count: int = Field(ge=0)
    voice_first_try_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    hours_saved_estimate: float = Field(ge=0.0)


class AchievementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    unlocked_at: datetime
    tier: Annotated[str, StringConstraints(pattern=r"^(mastery|reflection|social)$")]


class SyncPayload(BaseModel):
    """Incoming sync data. ``extra='forbid'`` is the central PII wall.

    Required fields:
    - ``ts_ms``: client send time in milliseconds since epoch (UTC).
      Server rejects when |now - ts_ms| > replay_window.
    - ``display_name``: redundant with the ``identity`` table, because the
      user must be able to change it without re-registering (Plan
      §C-Decision-2).
    """
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    daily_stats: list[DailyStatsItem] = Field(default_factory=list)
    achievements: list[AchievementItem] = Field(default_factory=list)
    bio: Annotated[str, StringConstraints(max_length=1000)] | None = None


class SyncAck(BaseModel):
    accepted: bool
    daily_stats_count: int
    achievements_count: int
    received_at: datetime


# ----------------------------------------------------------------------
# /api/v1/me
# ----------------------------------------------------------------------

class MeResponse(BaseModel):
    pubkey: PubkeyHex
    display_name: str
    created_at: datetime
    last_sync_at: datetime | None
    push_count: int


# ----------------------------------------------------------------------
# Generic error
# ----------------------------------------------------------------------

class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    extra: dict[str, Any] | None = None


# ----------------------------------------------------------------------
# Phase D — Pairing
# ----------------------------------------------------------------------

class PairInitiateRequest(BaseModel):
    """Admin generates a pair URL. Display name optional for debug displays."""
    note: str | None = None


class PairInitiateResponse(BaseModel):
    token: str
    url: str
    expires_at: datetime
    owner_pubkey: PubkeyHex


class PairAcceptRequest(BaseModel):
    """Friend's backend calls owner's /pair/accept with the token + its own data."""
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, StringConstraints(min_length=16, max_length=64)]
    friend_pubkey: PubkeyHex
    friend_url: Annotated[str, StringConstraints(min_length=4, max_length=500)]
    friend_display_name: Annotated[str, StringConstraints(min_length=1, max_length=120)]


class PairAcceptResponse(BaseModel):
    accepted: bool
    owner_pubkey: PubkeyHex
    owner_url: str
    owner_display_name: str
    paired_at: datetime


class FriendItem(BaseModel):
    pubkey: PubkeyHex
    url: str
    display_name: str
    paired_at: datetime
    last_pull_at: datetime | None
    pull_interval_s: int


class FriendsListResponse(BaseModel):
    friends: list[FriendItem]


# ----------------------------------------------------------------------
# Phase D — Activity / Stories / Visibility
# ----------------------------------------------------------------------

VISIBILITY_VALUES = ("private", "friends", "public")
VisibilityStr = Annotated[
    str,
    StringConstraints(pattern=r"^(private|friends|public)$"),
]


class ActivityCreateRequest(BaseModel):
    """Creation of a new activity by the owner.

    The server's extra='forbid' wall prevents contamination with tool
    outputs or voice text — the owner's frontend must sanitize that
    itself; the server accepts no additional fields.
    """
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    kind: Annotated[str, StringConstraints(pattern=r"^(achievement_unlocked|story|milestone)$")]
    payload: dict[str, Any] = Field(default_factory=dict)
    visibility: VisibilityStr = "friends"
    # Stories: 24h (server enforced). Other kinds: None.
    expires_in_hours: int | None = Field(default=None, ge=1, le=168)


class ActivityItemDTO(BaseModel):
    id: str
    author_pubkey: PubkeyHex
    author_display_name: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime
    visibility: VisibilityStr
    expires_at: datetime | None
    # Owner sees counts as numbers, others only get ``reactions_seen=true|false``.
    reaction_counts: dict[str, int] | None = None
    has_reactions: bool = False


class FeedResponse(BaseModel):
    items: list[ActivityItemDTO]
    sort: Annotated[str, StringConstraints(pattern=r"^(interesting|latest)$")]
    server_now: datetime


# ----------------------------------------------------------------------
# Phase D — Reactions
# ----------------------------------------------------------------------

class ReactionRequest(BaseModel):
    """Reactor → owner. Reactor's backend forwards this to the author's API.

    ``author_pubkey`` references the item author. The owner's backend uses
    this to find the right friend's backend in its ``friends`` table. The
    signature is computed over this whole body — if the frontend lies
    here, the signature verification fails on the friend's side (item_id
    doesn't match the signed payload).
    """
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    item_id: Annotated[str, StringConstraints(min_length=4, max_length=40)]
    reaction: Annotated[str, StringConstraints(pattern=r"^(rocket|brain|fire)$")]
    author_pubkey: PubkeyHex


class ReactionAck(BaseModel):
    accepted: bool


# ----------------------------------------------------------------------
# Phase D — Federation Inbound
# ----------------------------------------------------------------------

class InboundReactionRequest(BaseModel):
    """Friend's backend pushes a reaction to the owner's backend.

    Identical fields to ``ReactionRequest`` — only the signature is
    verified by the receiver against the reactor's pubkey.
    """
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    item_id: Annotated[str, StringConstraints(min_length=4, max_length=40)]
    reaction: Annotated[str, StringConstraints(pattern=r"^(rocket|brain|fire)$")]
    author_pubkey: PubkeyHex


class ForgetMeAck(BaseModel):
    deleted_friendship: bool
    deleted_activities: int
    deleted_reactions: int


# ----------------------------------------------------------------------
# Phase D — Stories (separate route, Plan §D-Spec)
# ----------------------------------------------------------------------

class StoryCreateRequest(BaseModel):
    """Wrapper around ``ActivityCreateRequest`` with a fixed kind=story.

    For the owner, ``POST /api/v1/stories`` is semantically clearer than a
    generic activity call with a kind parameter. Internally the server
    builds an ``ActivityCreateRequest(kind='story', expires_in_hours=24)``
    from this.
    """
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    text: Annotated[str, StringConstraints(min_length=1, max_length=280)]
    visibility: VisibilityStr = "friends"


# ----------------------------------------------------------------------
# Phase D — Friend Update
# ----------------------------------------------------------------------

class FriendUpdateRequest(BaseModel):
    """Per-friend sync interval setting (Plan §D-Spec)."""
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    pull_interval_s: int = Field(ge=60, le=3600)
