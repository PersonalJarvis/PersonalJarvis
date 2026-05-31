# === F-FRIENDS [F4] · feature/friends-section · the maintainer-2026-05-01 ===
"""Unit-Tests fuer :class:`jarvis.friends.status_filter.StatusFilter`.

Branch-portable: die echten Bus-Events (VoiceSessionStarted, MissionStarted,
OpenClawTaskStarted, ...) existieren auf diesem Branch eventuell noch nicht.
Wir bauen Fake-DataClass-Events mit den passenden Class-Names — der Filter
arbeitet ueber ``type(event).__name__`` + ``hasattr``, also reicht das.

Fokus der Tests:

- Hard-Blacklist absoluter Vorrang (KRITISCH).
- Profile-Whitelists sauber separiert.
- Field-Filtering blockiert Datenleak ueber unerwartete Event-Felder.
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis.friends.status_filter import HARD_BLACKLIST, PROFILES, StatusFilter

# ----------------------------------------------------------------------
# Fake-Events: Class-Name matched echte Bus-Event-Namen, Felder per Plan.
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
    internal_prompt: str = "leak me"  # darf NICHT durchkommen


@dataclass
class MissionCompleted:
    timestamp_ns: int = 4_000
    title: str = "Refactor Brain Module"
    success: bool = True
    cost_usd: float = 12.34  # darf NICHT durchkommen


@dataclass
class OpenClawTaskStarted:
    timestamp_ns: int = 5_000
    summary: str = "Implementiert F4-Pipeline"
    utterance: str = "leak me"  # darf NICHT durchkommen
    private_secret: str = "do-not-leak"  # erfundenes Feld, darf NICHT durchkommen


@dataclass
class OpenClawTaskCompleted:
    timestamp_ns: int = 6_000
    summary: str = "Done"
    success: bool = True


# ----------------------------------------------------------------------
# Hard-Blacklist: KRITISCHE Tests
# ----------------------------------------------------------------------


def test_hard_blacklist_blocks_in_minimal() -> None:
    """UtteranceCaptured darf im 'minimal'-Profile NIE durchkommen."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(event, "minimal")
    assert result is None


def test_hard_blacklist_blocks_in_standard() -> None:
    """UtteranceCaptured darf im 'standard'-Profile NIE durchkommen."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(event, "standard")
    assert result is None


def test_hard_blacklist_blocks_in_detailed() -> None:
    """UtteranceCaptured darf im 'detailed'-Profile NIE durchkommen."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(event, "detailed")
    assert result is None


def test_hard_blacklist_blocks_with_custom_whitelist() -> None:
    """Custom-Whitelist darf Hard-Blacklist NICHT umgehen (AP-1/AP-11)."""
    event = UtteranceCaptured()
    result = StatusFilter.filter(
        event, "detailed", custom_whitelist=["UtteranceCaptured"]
    )
    assert result is None


def test_hard_blacklist_blocks_action_executed() -> None:
    """ActionExecuted ist in HARD_BLACKLIST — keine Aktionen leaken."""
    event = ActionExecuted()
    result = StatusFilter.filter(event, "detailed")
    assert result is None


def test_hard_blacklist_blocks_memory_updated() -> None:
    """MemoryUpdated ist in HARD_BLACKLIST — keine Memory-Mutationen leaken."""
    event = MemoryUpdated()
    result = StatusFilter.filter(event, "detailed")
    assert result is None


def test_hard_blacklist_blocks_transcript_final() -> None:
    """TranscriptFinal in HARD_BLACKLIST — STT-Output verlaesst Maschine NIE."""
    event = TranscriptFinal()
    result = StatusFilter.filter(
        event, "detailed", custom_whitelist=["TranscriptFinal"]
    )
    assert result is None


def test_hard_blacklist_constant_completeness() -> None:
    """Sanity-Check: alle vom Plan genannten Hard-Blacklist-Eintraege da."""
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
# Profile-Whitelists
# ----------------------------------------------------------------------


def test_minimal_passes_voice_session_started() -> None:
    """'minimal' laesst VoiceSessionStarted durch."""
    event = VoiceSessionStarted()
    result = StatusFilter.filter(event, "minimal")
    assert result is not None
    assert result.event_type == "VoiceSessionStarted"
    assert result.profile_used == "minimal"
    assert result.timestamp_ns == 1_000


def test_minimal_blocks_mission_started() -> None:
    """'minimal' blockiert MissionStarted (Mission-Tracking erst ab 'standard')."""
    event = MissionStarted()
    result = StatusFilter.filter(event, "minimal")
    assert result is None


def test_minimal_blocks_openclaw() -> None:
    """'minimal' blockiert Sub-Jarvis-Events (erst 'detailed')."""
    event = OpenClawTaskStarted()
    result = StatusFilter.filter(event, "minimal")
    assert result is None


def test_standard_passes_mission_started() -> None:
    """'standard' laesst MissionStarted mit erlaubten Feldern durch."""
    event = MissionStarted()
    result = StatusFilter.filter(event, "standard")
    assert result is not None
    assert result.event_type == "MissionStarted"
    assert result.profile_used == "standard"
    assert result.fields == {"title": "Refactor Brain Module"}
    # internal_prompt darf NICHT durchkommen
    assert "internal_prompt" not in result.fields


def test_standard_passes_mission_completed_with_success() -> None:
    """'standard' enthaelt fuer MissionCompleted: title + success."""
    event = MissionCompleted()
    result = StatusFilter.filter(event, "standard")
    assert result is not None
    assert result.fields == {"title": "Refactor Brain Module", "success": True}
    # cost_usd darf NICHT durchkommen
    assert "cost_usd" not in result.fields


def test_standard_blocks_openclaw() -> None:
    """'standard' blockiert OpenClawTaskStarted."""
    event = OpenClawTaskStarted()
    result = StatusFilter.filter(event, "standard")
    assert result is None


def test_detailed_passes_openclaw_with_summary_only() -> None:
    """'detailed' laesst OpenClawTaskStarted durch, ABER nur summary-Field.

    KRITISCH: utterance + private_secret im Event duerfen NICHT durchkommen.
    """
    event = OpenClawTaskStarted()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    assert result.event_type == "OpenClawTaskStarted"
    assert result.fields == {"summary": "Implementiert F4-Pipeline"}
    assert "utterance" not in result.fields
    assert "private_secret" not in result.fields


def test_no_data_leak_via_unknown_field() -> None:
    """Erfundene Felder im Event leaken NICHT — Filter ist Whitelist-only."""
    event = OpenClawTaskStarted()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    # Nur summary darf raus
    assert set(result.fields.keys()) == {"summary"}


def test_detailed_passes_voice_session_ended() -> None:
    event = VoiceSessionEnded()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    assert result.event_type == "VoiceSessionEnded"
    assert result.timestamp_ns == 2_000


def test_detailed_passes_openclaw_completed() -> None:
    event = OpenClawTaskCompleted()
    result = StatusFilter.filter(event, "detailed")
    assert result is not None
    assert result.fields == {"summary": "Done", "success": True}


# ----------------------------------------------------------------------
# Custom-Whitelist-Verhalten
# ----------------------------------------------------------------------


def test_custom_whitelist_extends_profile() -> None:
    """Custom-Whitelist + Profile-Default werden als Union behandelt."""

    @dataclass
    class CustomDebugEvent:
        timestamp_ns: int = 7_000

    event = CustomDebugEvent()
    # Ohne Custom: blockiert
    assert StatusFilter.filter(event, "minimal") is None
    # Mit Custom: durch
    result = StatusFilter.filter(
        event, "minimal", custom_whitelist=["CustomDebugEvent"]
    )
    assert result is not None
    assert result.event_type == "CustomDebugEvent"


def test_custom_whitelist_does_not_replace_profile() -> None:
    """Custom-Whitelist ergaenzt nur — Profile-Defaults bleiben aktiv."""
    event = VoiceSessionStarted()
    # Custom-Whitelist enthaelt VoiceSessionStarted nicht — soll trotzdem durch
    result = StatusFilter.filter(
        event, "minimal", custom_whitelist=["FooBarEvent"]
    )
    assert result is not None
    assert result.event_type == "VoiceSessionStarted"


# ----------------------------------------------------------------------
# Edge-Cases
# ----------------------------------------------------------------------


def test_invalid_profile_returns_none() -> None:
    """Unbekanntes Profile -> None statt Crash."""
    event = VoiceSessionStarted()
    # Type-Hint Literal erlaubt das nicht, aber Runtime kann das passieren
    result = StatusFilter.filter(event, "bogus")  # type: ignore[arg-type]
    assert result is None


def test_event_without_timestamp_ns_default_to_zero() -> None:
    """Event ohne timestamp_ns -> Filter setzt 0 (kein Crash)."""

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
    """Sanity: PROFILES enthaelt minimal/standard/detailed."""
    assert set(PROFILES.keys()) == {"minimal", "standard", "detailed"}


def test_detailed_blocks_blacklisted_event_present_in_custom() -> None:
    """Egal welche Events der User custom-whitelistet — Hard-Blacklist gewinnt."""
    event = ActionExecuted()
    result = StatusFilter.filter(
        event,
        "detailed",
        custom_whitelist=["ActionExecuted", "MemoryUpdated", "UtteranceCaptured"],
    )
    assert result is None
