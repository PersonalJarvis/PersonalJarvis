"""Tests for ``jarvis.brain.mission_command_gate.match_mission_command``.

AD-12 + AP-OC5 (see ``docs/openclaw-bridge.md``):
- Status phrases must be recognized, spawn suppressed.
- Cancel phrases must be recognized, mission-cancel instead of spawn.
- Smalltalk / context-free phrases must NOT match (a false positive
  would be a regression of the former spawn-reflex problem).
"""
from __future__ import annotations

import pytest

from jarvis.brain.mission_command_gate import (
    MissionCommandMatch,
    match_mission_command,
)


# ---------------------------------------------------------------------------
# Status detection (positive cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase",
    [
        "läuft das noch?",  # i18n-allow
        "Läuft das noch?",  # i18n-allow
        "laeuft das noch",  # i18n-allow
        "Läuft die Mission noch?",  # i18n-allow
        "Status?",
        "status",
        "Jarvis, status",
        "Status der Mission",  # i18n-allow
        "Status vom Sub",  # i18n-allow
        "Wie weit?",  # i18n-allow
        "Wie weit bist du?",  # i18n-allow
        "Wie weit sind wir?",  # i18n-allow
        "Wo stehen wir?",  # i18n-allow
        "Wo steht die Mission?",  # i18n-allow
        "Noch am Laufen?",  # i18n-allow
        "Immer noch dran?",  # i18n-allow
    ],
)
def test_status_de_positive(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is not None, f"expected match for: {phrase!r}"
    assert m.intent == "status"
    assert m.language == "de"


@pytest.mark.parametrize(
    "phrase",
    [
        "is it still running?",
        "Is the mission still running?",
        "are you still running?",
        "are we still going?",
        "what's the status?",
        "what is the status?",
        "how far are we?",
        "how far is it?",
        "any progress?",
        "progress?",
    ],
)
def test_status_en_positive(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is not None, f"expected match for: {phrase!r}"
    assert m.intent == "status"
    assert m.language == "en"


# ---------------------------------------------------------------------------
# Cancel detection (positive cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase",
    [
        "brich ab",  # i18n-allow
        "Brich ab",  # i18n-allow
        "Jarvis, brich ab",  # i18n-allow
        "Brich die Mission ab",  # i18n-allow
        "Brich den Auftrag ab",  # i18n-allow
        "Brich alles ab",  # i18n-allow
        "Stoppe die Mission",  # i18n-allow
        "Stop die Mission",  # i18n-allow
        "Stoppe OpenClaw",  # i18n-allow
        "Mission abbrechen",  # i18n-allow
        "Mission bitte abbrechen",  # i18n-allow
        "Auftrag abbrechen",  # i18n-allow
        "Abbruch der Mission",  # i18n-allow
        "Abbruch OpenClaw",  # i18n-allow
    ],
)
def test_cancel_de_positive(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is not None, f"expected match for: {phrase!r}"
    assert m.intent == "cancel"
    assert m.language == "de"


@pytest.mark.parametrize(
    "phrase",
    [
        "cancel the mission",
        "cancel openclaw",
        "cancel claw",
        "cancel the task",
        "stop the mission",
        "stop the task",
        "stop openclaw",
        "Stop OpenClaw",
        "Stop claw",
        "abort the mission",
        "abort openclaw",
        "kill the mission",
        "kill the task",
    ],
)
def test_cancel_en_positive(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is not None, f"expected match for: {phrase!r}"
    assert m.intent == "cancel"
    assert m.language == "en"


# ---------------------------------------------------------------------------
# Negative cases — must NOT match
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase",
    [
        "",
        "   ",
        "Hallo Jarvis",  # i18n-allow
        "Wie geht es dir?",  # i18n-allow
        "Was ist die Hauptstadt von Deutschland?",  # i18n-allow
        "Erzähl mir einen Witz",  # i18n-allow
        "Wie weit ist Berlin von Muenchen?",         # 'wie weit' but no mission-context word  # i18n-allow
        "Status der Wirtschaft",                       # status, but no mission reference  # i18n-allow
        "Stoppe die Musik",                            # 'stop' but music, not mission  # i18n-allow
        "Lass das stoppen, das Lied",                  # generic stop  # i18n-allow
        "Hör auf damit",                               # generic  # i18n-allow
        "Spiele weiter",                               # 'continue', but no status pattern  # i18n-allow
        "Cancel my dinner reservation",                # 'cancel' but no mission/task
        "Kill the music",                              # 'kill' but music
        "Run the script",                              # 'run' but no status
        "Open my browser",                             # generic action
        "Schreib eine E-Mail an Anna",                 # action verb, no status  # i18n-allow
        "What's the weather?",                         # status-like, but not 'the status'
    ],
)
def test_negative_no_match(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is None, f"unexpected match for: {phrase!r} -> {m!r}"


# ---------------------------------------------------------------------------
# Cancel takes priority over status on a double match
# ---------------------------------------------------------------------------

def test_cancel_wins_over_status() -> None:
    """When both patterns would match, cancel wins — an explicit stop
    instruction must not be degraded to a status read."""
    m = match_mission_command("Stop OpenClaw, wie weit bist du eigentlich?")  # i18n-allow
    assert m is not None
    assert m.intent == "cancel"


# ---------------------------------------------------------------------------
# Mission-ID extraction
# ---------------------------------------------------------------------------

def test_extract_uuid_v7() -> None:
    m = match_mission_command(
        "Status der Mission 0190f7a3-1234-7abc-9def-0123456789ab"  # i18n-allow
    )
    assert m is not None
    assert m.intent == "status"
    assert m.mission_id == "0190f7a3-1234-7abc-9def-0123456789ab"


def test_extract_uuid_normalized() -> None:
    """A UUID without hyphens is mapped to the standard format."""
    m = match_mission_command(
        "stop mission 0190f7a312347abc9def0123456789ab"
    )
    assert m is not None
    assert m.intent == "cancel"
    assert m.mission_id == "0190f7a3-1234-7abc-9def-0123456789ab"


def test_extract_short_alias() -> None:
    """Short aliases ('mission build-foo') are passed through raw."""
    m = match_mission_command("Status der Mission build-foo")  # i18n-allow
    assert m is not None
    assert m.mission_id == "build-foo"


def test_no_mission_id_means_all_active() -> None:
    """Without a mission hint, mission_id is None — the caller filters over all active missions."""
    m = match_mission_command("Läuft das noch?")  # i18n-allow
    assert m is not None
    assert m.mission_id is None


# ---------------------------------------------------------------------------
# Frozen dataclass contract
# ---------------------------------------------------------------------------

def test_match_is_frozen() -> None:
    m = MissionCommandMatch(intent="status")
    with pytest.raises(Exception):
        m.intent = "cancel"  # type: ignore[misc]
