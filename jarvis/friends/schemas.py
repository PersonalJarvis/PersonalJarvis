# === F-FRIENDS [F4] · feature/friends-section · ruben-2026-05-01 ===
"""Pydantic schemas for outbound status updates sent to friends.

A :class:`StatusUpdate` is the canonical outbound representation of a
bus event AFTER :class:`StatusFilter` has removed all disallowed fields.
The entries in the ``fields`` dict are already filtered — the recipient
sees only what their profile permits.

The schema is intentionally minimal: ``event_type`` is a plain string
(not an enum) so that new bus events can pass through the filter without
requiring a schema migration, and ``fields`` is a generic dict so that
per-event schemas do not need to be hardwired here.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import StatusProfile


class StatusUpdate(BaseModel):
    """A filtered bus event ready for delivery to a friend."""

    model_config = ConfigDict(frozen=True)

    event_type: str = Field(..., description="Class name of the original event")
    timestamp_ns: int = Field(..., description="Original timestamp_ns from the event")
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Filtered payload — only fields allowed by the profile.",
    )
    profile_used: StatusProfile = Field(
        ..., description="Profile that approved this update"
    )


__all__ = ["StatusUpdate"]
