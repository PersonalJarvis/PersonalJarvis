"""SQLAlchemy-Models.

Phase C: Identity + PushLog.
Phase D: Friend + PairToken + ActivityItem + Reaction.

Schema bleibt additiv via ``create_all`` — keine Migration noetig.
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
    """Eine registrierte Jarvis-Instanz (User).

    ``pubkey`` ist der einzige Identitaets-Anker (Plan §C-Decision-2: Pubkey-
    only). Der ``display_name`` darf sich pro Push aendern — er wird
    deshalb auch hier geupdatet und ist nicht der Primary Key.
    """

    __tablename__ = "identity"

    pubkey: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    bio: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class PushLog(Base):
    """Audit-Log eines erfolgreichen Sync-Calls.

    Wir speichern bewusst NUR Metadaten (welcher Pubkey, wann, wie viele
    Tage / Achievements im Payload), nicht den Payload selbst. Phase D wird
    den Payload in `friends_activity` umsortieren — bis dahin ist der
    Sync-Inhalt nur ein Acknowledge.
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
    """Freundschaftsverbindung des Owners zu einem anderen Backend.

    Single-Tenant-Modell (siehe Plan §C-Decision-2): es gibt **eine**
    Identity pro Backend, ``owner_pubkey`` ist also derselbe fuer alle
    ``Friend``-Rows. Wir speichern ihn trotzdem explizit, damit das Schema
    in der Zukunft skalierbar ist.
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
    """Time-limited Pair-Token. Plan §D-Decision-1: 10 min Gueltigkeit.

    Einmal-Verwendung: ``used_at`` wird beim Accept gesetzt, ein zweiter
    Accept mit demselben Token failed mit 401.
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
    """Eine Aktivitaet auf dem Owner-Board: achievement_unlocked, story, …

    ``visibility`` ist die Plan-§D-Sichtbarkeitsachse: ``private`` (nur
    Owner), ``friends`` (paired Friends), ``public`` (jeder).

    ``payload`` ist ein JSON-String mit kind-spezifischem Inhalt — z.B.
    ``{"achievement_id": "tool_master"}`` oder ``{"text": "what I worked on"}``.

    ``expires_at`` ist nur fuer Stories gesetzt (24 h, Plan §D).
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
    """Reaktion auf einen Activity-Item.

    Plan §D §0: Owner sieht eigene Reaction-Counts mit Zahl, andere User
    sehen nur die Reaction-Icons. Die Tabelle speichert die Rohdaten —
    die Visibility-Logik passiert beim Query.
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
