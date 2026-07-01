# === F-FRIENDS [F4] · feature/friends-section · alex-2026-05-01 ===
"""Unit tests for :class:`jarvis.friends.status_filter.StatusFilter`.

Branch-portable: the real bus events (VoiceSessionStarted, MissionStarted,
JarvisAgentTaskStarted, ...) may not exist yet on this branch.
We build fake dataclass events with matching class names — the filter
works via ``type(event).__name__`` + ``hasattr``, so that's enough.

Focus of the tests:

- Hard blacklist has absolute precedence (CRITICAL).
- Profile whitelists are cleanly separated.
- Field filtering blocks data leaks via unexpected event fields.
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis.friends.status_filter import HARD_BLACKLIST, PROFILES, StatusFilter

# ----------------------------------------------------------------------
# Fake events: class name matches real bus event names, fields per plan.
# ----------------------------------------------------------------------


@dataclass
class UtteranceCaptured:
    timestamp_ns: int = 1_000
    audio_ref: str = "very-secret-content"
    duration_ms: int = 500


@dataclass
class TranscriptFinal:
    timestamp_ns: int = 1_000
    transcript: str = "secret words from user"


@dataclass
class ActionExecuted:
    timestamp_ns: int = 1_000
    action: str = "open_browser"
    target: str = "https://private.bank/login"


@dataclass
class MemoryUpdated:
    timestamp_ns: int = 1_000
    key: str = "credit_card_number"


@dataclass
class VoiceSessionStarted:
    timestamp_ns: int = 1_000
    wake_keyword: str = "jarvis"


@dataclass
class VoiceSessionEnded:
    timestamp_ns: int = 2_000


@dataclass
class MissionStarted:
    timestamp_ns: int = 3_000
    title: str = "Refactor Brain Module"
    internal_prompt: str = "leak me"  # must NOT pass through


@dataclass
class MissionCompleted:
    timestamp_ns: int = 4_000
    title: str = "Refactor Brain Module"
    success: bool = True
    cost_usd: float = 12.34  # must NOT pass through


@dataclass
class JarvisAgentTaskStarted:
    timestamp_ns: int = 5_000
    summary: str = "Implementiert F4-Pipeline"
    utterance: str = "leak me"  # must NOT pass through
    private_secret: str = "do-not-leak"  # made-up field, must NOT pass through


@dataclass
class JarvisAgentTaskCompleted:
    timestamp_ns: int = 6_000
    summary: str = "Done"
    success: bool = True


# ----------------------------------------------------------------------
# Hard blacklist: CRITICAL tests
# ----------------------------------------------------------------------


def test_hard_blacklist_blocks_in_minimal() -> None:
    """UtteranceCaptured must NEVER pass through in the 'minimal' profile."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(event, "minimal")
    assert result is None


def test_hard_blacklist_blocks_in_standard() -> None:
    """UtteranceCaptured must NEVER pass through in the 'standard' profile."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(event, "standard")
    assert result is None


def test_hard_blacklist_blocks_in_detailed() -> None:
    """UtteranceCaptured must NEVER pass through in the 'detailed' profile."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(event, "detailed")
    assert result is None


def test_hard_blacklist_blocks_with_custom_whitelist() -> None:
    """A custom whitelist must NOT bypass the hard blacklist (AP-1/AP-11)."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(
        event, "detailed", custom_whitelist=["UtteranceCaptured"]
    )
    assert result is None


def test_hard_blacklist_blocks_action_executed() -> None:
    """ActionExecuted is in HARD_BLACKLIST — no actions may leak."""
    event = ActionExecuted()
    result = StatusFilter.filter(event, "detailed")
    assert result is None


def test_hard_blacklist_blocks_memory_updated() -> None:
    """MemoryUpdated is in HARD_BLACKLIST — no memory mutations may leak."""
    event = MemoryUpdated()
    result = StatusFilter.filter(event, "detailed")
    assert result is None


def test_hard_blacklist_blocks_transcript_final() -> None:
    """TranscriptFinal is in HARD_BLACKLIST — STT output NEVER leaves the machine."""
    event = TranscriptFinal()
    result = StatusFilter.filter(
        event, "detailed", custom_whitelist=["TranscriptFinal"]
    )
    assert result is None


def test_hard_blacklist_constant_completeness() -> None:
    """Sanity check: all hard-blacklist entries named in the plan are present."""
    required = {
        "UtteranceCaptured",
        "TranscriptFinal",
        "ActionProposed",
        "ActionExecuted",
        "ErrorOccurred",
        "ObservationCaptured",
        "MemoryUpdated",
    }
    assert required <= HARD_BLACKLIST


# ----------------------------------------------------------------------
# Profile whitelists
# ----------------------------------------------------------------------


def test_minimal_passes_voice_session_started() -> None:
    """'minimal' lets VoiceSessionStarted through."""
    event = VoiceSessionStarted()
    result = StatusFilter.filter(event, "minimal")
    assert result is not None
    assert result.event_type == "VoiceSessionStarted"
    assert result.profile_used == "minimal"
    assert result.timestamp_ns == 1_000


def test_minimal_blocks_mission_started() -> None:
    """'minimal' blocks MissionStarted (mission tracking only starts at 'standard')."""
    event = MissionStarted()
    result = StatusFilter.filter(event, "minimal")
    assert result is None


def test_minimal_blocks_openclaw() -> None:
    """'minimal' blocks Jarvis-Agent events (only allowed from 'detailed')."""
    event = JarvisAgentTaskStarted()
    result = StatusFilter.filter(event, "minimal")
    assert result is None


def test_standard_passes_mission_started() -> None:
    """'standard' lets MissionStarted through with its allowed fields."""
    event = MissionStarted()
    result = StatusFilter.filter(event, "standard")
    assert result is not None
    assert result.event_type == "MissionStarted"
    assert result.profile_used == "standard"
    assert result.fields == {"title": "Refactor Brain Module"}
    # internal_prompt must NOT pass through
    assert "internal_prompt" not in result.fields


def test_standard_passes_mission_completed_with_success() -> None:
    """'standard' includes for MissionCompleted: title + success."""
    event = MissionCompleted()
    result = StatusFilter.filter(event, "standard")
    assert result is not None
    assert result.fields == {"title": "Refactor Brain Module", "success": True}
    # cost_usd must NOT pass through
    assert "cost_usd" not in result.fields


def test_standard_blocks_openclaw() -> None:
    """'standard' blocks JarvisAgentTaskStarted."""
    event = JarvisAgentTaskStarted()
    result = StatusFilter.filter(event, "standard")
    assert result is None


def test_detailed_passes_openclaw_with_summary_only() -> None:
    """'detailed' lets JarvisAgentTaskStarted through, BUT only the summary field.

    CRITICAL: utterance + private_secret on the event must NOT pass through.
    """
    event = JarvisAgentTaskStarted()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    assert result.event_type == "JarvisAgentTaskStarted"
    assert result.fields == {"summary": "Implementiert F4-Pipeline"}
    assert "utterance" not in result.fields
    assert "private_secret" not in result.fields


def test_no_data_leak_via_unknown_field() -> None:
    """Made-up fields on the event do NOT leak — the filter is whitelist-only."""
    event = JarvisAgentTaskStarted()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    # Only summary may pass through
    assert set(result.fields.keys()) == {"summary"}


def test_detailed_passes_voice_session_ended() -> None:
    event = VoiceSessionEnded()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    assert result.event_type == "VoiceSessionEnded"
    assert result.timestamp_ns == 2_000


def test_detailed_passes_openclaw_completed() -> None:
    event = JarvisAgentTaskCompleted()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    assert result.fields == {"summary": "Done", "success": True}


# ----------------------------------------------------------------------
# Custom whitelist behavior
# ----------------------------------------------------------------------


def test_custom_whitelist_extends_profile() -> None:
    """Custom whitelist + profile default are treated as a union."""

    @dataclass
    class CustomDebugEvent:
        timestamp_ns: int = 7_000

    event = CustomDebugEvent()
    # Without custom: blocked
    assert StatusFilter.filter(event, "minimal") is None
    # With custom: passes through
    result = StatusFilter.filter(
        event, "minimal", custom_whitelist=["CustomDebugEvent"]
    )
    assert result is not None
    assert result.event_type == "CustomDebugEvent"


def test_custom_whitelist_does_not_replace_profile() -> None:
    """Custom whitelist only extends — profile defaults stay active."""
    event = VoiceSessionStarted()
    # Custom whitelist does not contain VoiceSessionStarted — should still pass through
    result = StatusFilter.filter(
        event, "minimal", custom_whitelist=["FooBarEvent"]
    )
    assert result is not None
    assert result.event_type == "VoiceSessionStarted"


# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------


def test_invalid_profile_returns_none() -> None:
    """Unknown profile -> None instead of a crash."""
    event = VoiceSessionStarted()
    # The type-hint Literal disallows this, but it can happen at runtime
    result = StatusFilter.filter(event, "bogus")  # type: ignore[arg-type]
    assert result is None


def test_event_without_timestamp_ns_default_to_zero() -> None:
    """Event without timestamp_ns -> filter defaults it to 0 (no crash)."""

    @dataclass
    class TimestampLess:
        pass

    event = TimestampLess()
    result = StatusFilter.filter(
        event, "minimal", custom_whitelist=["TimestampLess"]
    )
    assert result is not None
    assert result.timestamp_ns == 0


def test_profiles_constant_has_all_three_levels() -> None:
    """Sanity: PROFILES contains minimal/standard/detailed."""
    assert set(PROFILES.keys()) == {"minimal", "standard", "detailed"}


def test_detailed_blocks_blacklisted_event_present_in_custom() -> None:
    """No matter which events the user custom-whitelists — the hard blacklist wins."""
    event = ActionExecuted()
    result = StatusFilter.filter(
        event,
        "detailed",
        custom_whitelist=["ActionExecuted", "MemoryUpdated", "UtteranceCaptured"],
    )
    assert result is None
