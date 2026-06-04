"""Pydantic-Request/Response-Modelle.

Sicherheit: ``SyncPayload`` enthaelt **explizit nur** sichere Aggregat-
Felder. Wenn ein Sync-Call Felder mit verbotenen Namen mitsendet
(``text``, ``transcript``, ``args``, ``output_preview`` …), wird er vom
``models_extra='forbid'`` abgelehnt. Das ist die Server-seitige Spiegelung
von ``BoardAggregator.export_all_for_federation()`` (Plan §A-Smoke
„test_no_pii_in_aggregated_stats").
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
    """Eine Tages-Aggregation. Spiegelt das BoardAggregator-Format aus
    ``export_all_for_federation()`` — sichere Felder only.
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
    """Eingehende Sync-Daten. ``extra='forbid'`` ist die zentrale PII-Wand.

    Pflichtfelder:
    - ``ts_ms``: Client-Sendezeit in Millisekunden seit Epoch (UTC).
      Server lehnt ab, wenn |now - ts_ms| > replay_window.
    - ``display_name``: redundant zur ``identity``-Tabelle, weil der User
      ihn ohne Re-Registrierung aendern koennen muss (Plan §C-Decision-2).
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
# Generischer Error
# ----------------------------------------------------------------------

class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    extra: dict[str, Any] | None = None


# ----------------------------------------------------------------------
# Phase D — Pairing
# ----------------------------------------------------------------------

class PairInitiateRequest(BaseModel):
    """Admin generiert Pair-URL. Display-Name optional fuer Debug-Anzeigen."""
    note: str | None = None


class PairInitiateResponse(BaseModel):
    token: str
    url: str
    expires_at: datetime
    owner_pubkey: PubkeyHex


class PairAcceptRequest(BaseModel):
    """Friend's Backend ruft Owner's /pair/accept mit dem Token + eigenen Daten."""
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
    """Erstellung einer neuen Activity vom Owner.

    Server-extra='forbid'-Wand verhindert Kontamination mit Tool-Outputs
    oder Voice-Texten — der Owner-Frontend muss das selbst saubern, der
    Server akzeptiert keine zusaetzlichen Felder.
    """
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    kind: Annotated[str, StringConstraints(pattern=r"^(achievement_unlocked|story|milestone)$")]
    payload: dict[str, Any] = Field(default_factory=dict)
    visibility: VisibilityStr = "friends"
    # Stories: 24h (Server enforced). Andere Kinds: None.
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
    # Owner sieht Counts mit Zahlen, andere bekommen nur ``reactions_seen=true|false``.
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
    """Reactor → Owner. Reactor's Backend forwardet das hier zur Author-API.

    ``author_pubkey`` referenziert den Item-Author. Owner-Backend nutzt das,
    um in seiner ``friends``-Tabelle den richtigen Friend-Backend zu finden.
    Die Sig wird auf diesem ganzen body gemacht — wenn das Frontend hier
    luegt, scheitert die Sig-Verify auf Friend-Seite (item_id stimmt nicht
    zum signierten payload).
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
    """Friend's Backend pusht eine Reaktion an Owner's Backend.

    Identische Felder wie ``ReactionRequest`` — nur die Sig wird beim
    Empfaenger gegen den Reactor's Pubkey verifiziert.
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
    """Wrapper um ``ActivityCreateRequest`` mit fixem kind=story.

    Fuer den Owner ist ``POST /api/v1/stories`` semantisch klarer als ein
    generischer activity-call mit kind-Parameter. Server intern bauen wir
    daraus einen ``ActivityCreateRequest(kind='story', expires_in_hours=24)``.
    """
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    text: Annotated[str, StringConstraints(min_length=1, max_length=280)]
    visibility: VisibilityStr = "friends"


# ----------------------------------------------------------------------
# Phase D — Friend Update
# ----------------------------------------------------------------------

class FriendUpdateRequest(BaseModel):
    """Per-Friend-Sync-Interval-Setting (Plan §D-Spec)."""
    model_config = ConfigDict(extra="forbid")

    ts_ms: int = Field(ge=0)
    pull_interval_s: int = Field(ge=60, le=3600)
