# === F-FRIENDS [F4] · feature/friends-section · the maintainer-2026-05-01 ===
"""StatusFilter: hardcoded profile whitelists and an absolute hard blacklist.

Architecture (plan F4 + Phase-7 plan AP-1/AP-11):

- :data:`PROFILES` contains the three sharing profiles as hardcoded
  ``frozenset`` whitelists. A user may extend these via ``custom_whitelist``
  in :class:`FriendStatusPermission` — but not replace them.
- :data:`HARD_BLACKLIST` contains event types that NEVER reach a friend,
  regardless of the active profile or any ``custom_whitelist`` entry.
  This is the central privacy guard for raw utterances, stack traces,
  memory updates, and tool arguments.

The class is intentionally stateless (all methods are ``staticmethod``) —
the filter depends only on the event and profile; no I/O, no caches.
"""
from __future__ import annotations

from typing import Any

from .models import StatusProfile
from .schemas import StatusUpdate

# ----------------------------------------------------------------------
# Profile-Whitelists
# ----------------------------------------------------------------------

PROFILES: dict[StatusProfile, dict[str, Any]] = {
    "minimal": {
        "events": frozenset({"VoiceSessionStarted", "VoiceSessionEnded"}),
        "fields": {
            "VoiceSessionStarted": frozenset({"timestamp_ns"}),
            "VoiceSessionEnded": frozenset({"timestamp_ns"}),
        },
    },
    "standard": {
        "events": frozenset(
            {
                "VoiceSessionStarted",
                "VoiceSessionEnded",
                "MissionStarted",
                "MissionCompleted",
            }
        ),
        "fields": {
            "VoiceSessionStarted": frozenset({"timestamp_ns"}),
            "VoiceSessionEnded": frozenset({"timestamp_ns"}),
            "MissionStarted": frozenset({"timestamp_ns", "title"}),
            "MissionCompleted": frozenset({"timestamp_ns", "title", "success"}),
        },
    },
    "detailed": {
        "events": frozenset(
            {
                "VoiceSessionStarted",
                "VoiceSessionEnded",
                "MissionStarted",
                "MissionCompleted",
                "OpenClawTaskStarted",
                "OpenClawTaskCompleted",
            }
        ),
        "fields": {
            "VoiceSessionStarted": frozenset({"timestamp_ns"}),
            "VoiceSessionEnded": frozenset({"timestamp_ns"}),
            "MissionStarted": frozenset({"timestamp_ns", "title"}),
            "MissionCompleted": frozenset({"timestamp_ns", "title", "success"}),
            "OpenClawTaskStarted": frozenset({"timestamp_ns", "summary"}),
            "OpenClawTaskCompleted": frozenset(
                {"timestamp_ns", "summary", "success"}
            ),
        },
    },
}

# ----------------------------------------------------------------------
# Hard-Blacklist — absoluter Vorrang
# ----------------------------------------------------------------------

HARD_BLACKLIST: frozenset[str] = frozenset(
    {
        # Raw speech data
        "UtteranceCaptured",
        "TranscriptFinal",
        "TranscriptPartial",
        # Actions + observations (potentially sensitive content)
        "ActionProposed",
        "ActionExecuted",
        "ObservationCaptured",
        # Error details (stack traces, paths)
        "ErrorOccurred",
        # Memory mutations
        "MemoryUpdated",
    }
)


class StatusFilter:
    """Stateless filter: Event -> StatusUpdate | None."""

    @staticmethod
    def filter(
        event: Any,
        profile: StatusProfile,
        custom_whitelist: list[str] | None = None,
    ) -> StatusUpdate | None:
        """Returns a :class:`StatusUpdate` if the event passes the filter, otherwise None.

        Evaluation order:

        1. **Hard blacklist** takes absolute precedence — even a custom whitelist
           cannot override it (AP-1/AP-11 constraint-self-bypass protection).
        2. Profile default events OR custom whitelist (union) determines whether
           the event type is permitted.
        3. Fields are filtered using the profile field whitelist; any field that
           does not exist on the event is silently omitted (duck-typing).
        """
        event_type = type(event).__name__

        # 1. Hard blacklist always wins
        if event_type in HARD_BLACKLIST:
            return None

        profile_def = PROFILES.get(profile)
        if profile_def is None:
            return None

        # 2. Determine allowed events (profile + optional custom)
        allowed_events: frozenset[str] = profile_def["events"]
        if custom_whitelist is not None:
            allowed_events = (
                allowed_events | frozenset(custom_whitelist)
            ) - HARD_BLACKLIST

        if event_type not in allowed_events:
            return None

        # 3. Filter fields (default = timestamp_ns only if event is not in the profile field map)
        allowed_fields: frozenset[str] = profile_def["fields"].get(
            event_type, frozenset({"timestamp_ns"})
        )
        out_fields: dict[str, Any] = {}
        for field_name in allowed_fields:
            if field_name == "timestamp_ns":
                # timestamp_ns lives at the top level of StatusUpdate, not inside fields
                continue
            if hasattr(event, field_name):
                out_fields[field_name] = getattr(event, field_name)

        return StatusUpdate(
            event_type=event_type,
            timestamp_ns=int(getattr(event, "timestamp_ns", 0) or 0),
            fields=out_fields,
            profile_used=profile,
        )


__all__ = ["StatusFilter", "PROFILES", "HARD_BLACKLIST"]
