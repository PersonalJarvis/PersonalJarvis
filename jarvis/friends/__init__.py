# === F-FRIENDS [F0] · feature/friends-section · ruben-2026-04-30 ===
"""Friends layer: local persistence for friends and channel associations.

A friend is a real person (Telegram contact, another Jarvis user, or a
pure status observer). The identity is cross-channel — the same friend can
be linked simultaneously via a Telegram chat ID and via a Jarvis public key.

Status-sharing permissions are persisted here (per-friend profile
``minimal``/``standard``/``detailed``); the hard blacklist (which bus events
NEVER leave the instance) lives in :mod:`jarvis.friends.status_filter` as a
hard-coded allowlist (see plan AP-1/AP-11).
"""
from __future__ import annotations

from .messages import DirectMessage, DirectMessageStore
from .models import (
    Friend,
    FriendChannel,
    FriendChannelKind,
    FriendStatusPermission,
    StatusProfile,
)

__all__ = [
    "DirectMessage",
    "DirectMessageStore",
    "Friend",
    "FriendChannel",
    "FriendChannelKind",
    "FriendStatusPermission",
    "StatusProfile",
]
