"""SQLAlchemy models.

Phase C: Identity + PushLog.
Phase D: Friend + PairToken + ActivityItem + Reaction.

The schema stays additive via ``create_all`` — no migration needed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Identity(Base):
    """A registered Jarvis instance (user).

    ``pubkey`` is the only identity anchor (Plan §C-Decision-2: pubkey
    only). The ``display_name`` may change per push — that's why it's
    updated here too and is not the primary key.
    """

    __tablename__ = "identity"

    pubkey: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    bio: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PushLog(Base):
    """Audit log of a successful sync call.

    We deliberately store ONLY metadata (which pubkey, when, how many
    days / achievements in the payload), not the payload itself. Phase D
    will re-sort the payload into `friends_activity` — until then the
    sync content is just an acknowledge.
    """

    __tablename__ = "push_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pubkey: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    daily_stats_count: Mapped[int] = mapped_column(Integer, default=0)
    achievements_count: Mapped[int] = mapped_column(Integer, default=0)
    payload_ts_ms: Mapped[int] = mapped_column(Integer)


Index("ix_pushlog_pubkey_received", PushLog.pubkey, PushLog.received_at)


# ----------------------------------------------------------------------
# Phase D — Friends + Activity + Reactions
# ----------------------------------------------------------------------

class Friend(Base):
    """The owner's friendship link to another backend.

    Single-tenant model (see Plan §C-Decision-2): there is **one**
    identity per backend, so ``owner_pubkey`` is the same for all
    ``Friend`` rows. We still store it explicitly so the schema stays
    scalable in the future.
    """

    __tablename__ = "friends"

    owner_pubkey: Mapped[str] = mapped_column(
        String(64), ForeignKey("identity.pubkey", ondelete="CASCADE"),
        primary_key=True,
    )
    friend_pubkey: Mapped[str] = mapped_column(String(64), primary_key=True)
    friend_url: Mapped[str] = mapped_column(String(500))
    friend_display_name: Mapped[str] = mapped_column(String(120), default="")
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pull_interval_s: Mapped[int] = mapped_column(Integer, default=120)


class PairToken(Base):
    """Time-limited pair token. Plan §D-Decision-1: 10 min validity.

    Single use: ``used_at`` is set on accept, a second accept with the
    same token fails with 401.
    """

    __tablename__ = "pair_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_pubkey: Mapped[str] = mapped_column(
        String(64), ForeignKey("identity.pubkey", ondelete="CASCADE"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ActivityItem(Base):
    """An activity on the owner's board: achievement_unlocked, story, …

    ``visibility`` is the Plan-§D visibility axis: ``private`` (owner
    only), ``friends`` (paired friends), ``public`` (everyone).

    ``payload`` is a JSON string with kind-specific content — e.g.
    ``{"achievement_id": "tool_master"}`` or ``{"text": "what I worked on"}``.

    ``expires_at`` is only set for stories (24 h, Plan §D).
    """

    __tablename__ = "activity_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    author_pubkey: Mapped[str] = mapped_column(
        String(64), ForeignKey("identity.pubkey", ondelete="CASCADE"), index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    visibility: Mapped[str] = mapped_column(String(16), default="friends")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


Index("ix_activity_visibility_created", ActivityItem.visibility, ActivityItem.created_at)


class Reaction(Base):
    """A reaction to an activity item.

    Plan §D §0: the owner sees their own reaction counts as a number,
    other users only see the reaction icons. The table stores the raw
    data — the visibility logic happens at query time.
    """

    __tablename__ = "reactions"
    __table_args__ = (
        UniqueConstraint("item_id", "reactor_pubkey", "reaction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("activity_items.id", ondelete="CASCADE"), index=True,
    )
    reactor_pubkey: Mapped[str] = mapped_column(String(64), index=True)
    reaction: Mapped[str] = mapped_column(String(16))         # "rocket" | "brain" | "fire"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
