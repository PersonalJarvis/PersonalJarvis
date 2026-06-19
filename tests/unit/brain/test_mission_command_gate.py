"""Tests fuer ``jarvis.brain.mission_command_gate.match_mission_command``.

AD-12 + AP-OC5 (siehe ``docs/openclaw-bridge.md``):
- Status-Phrasen muessen erkannt werden, Spawn unterdrueckt.
- Cancel-Phrasen muessen erkannt werden, Mission-Cancel statt Spawn.
- Smalltalk-/Kontextfreie-Phrasen DUERFEN NICHT matchen (False-Positive
  waere eine Regression auf das ehemalige Spawn-Reflex-Problem).
"""
from __future__ import annotations

import pytest

from jarvis.brain.mission_command_gate import (
    MissionCommandMatch,
    match_mission_command,
)


# ---------------------------------------------------------------------------
# Status-Erkennung (positive cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase",
    [
        "läuft das noch?",
        "Läuft das noch?",
        "laeuft das noch",
        "Läuft die Mission noch?",
        "Status?",
        "status",
        "Jarvis, status",
        "Status der Mission",
        "Status vom Sub",
        "Wie weit?",
        "Wie weit bist du?",
        "Wie weit sind wir?",
        "Wo stehen wir?",
        "Wo steht die Mission?",
        "Noch am Laufen?",
        "Immer noch dran?",
    ],
)
def test_status_de_positive(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is not None, f"erwartet Match fuer: {phrase!r}"
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
    assert m is not None, f"erwartet Match fuer: {phrase!r}"
    assert m.intent == "status"
    assert m.language == "en"


# ---------------------------------------------------------------------------
# Cancel-Erkennung (positive cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase",
    [
        "brich ab",
        "Brich ab",
        "Jarvis, brich ab",
        "Brich die Mission ab",
        "Brich den Auftrag ab",
        "Brich alles ab",
        "Stoppe die Mission",
        "Stop die Mission",
        "Stoppe OpenClaw",
        "Mission abbrechen",
        "Mission bitte abbrechen",
        "Auftrag abbrechen",
        "Abbruch der Mission",
        "Abbruch OpenClaw",
    ],
)
def test_cancel_de_positive(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is not None, f"erwartet Match fuer: {phrase!r}"
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
    assert m is not None, f"erwartet Match fuer: {phrase!r}"
    assert m.intent == "cancel"
    assert m.language == "en"


# ---------------------------------------------------------------------------
# Negative cases — DUERFEN NICHT matchen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase",
    [
        "",
        "   ",
        "Hallo Jarvis",
        "Wie geht es dir?",
        "Was ist die Hauptstadt von Deutschland?",
        "Erzähl mir einen Witz",
        "Wie weit ist Berlin von Muenchen?",         # 'wie weit' aber ohne Mission-Kontext-Wort
        "Status der Wirtschaft",                       # Status, aber kein Mission-Bezug
        "Stoppe die Musik",                            # 'stop' aber Musik, nicht Mission
        "Lass das stoppen, das Lied",                  # generic stop
        "Hör auf damit",                               # generic
        "Spiele weiter",                               # weiter, aber kein Status-Pattern
        "Cancel my dinner reservation",                # 'cancel' aber kein Mission/Task
        "Kill the music",                              # 'kill' aber Musik
        "Run the script",                              # 'run' aber kein Status
        "Open my browser",                             # generic action
        "Schreib eine E-Mail an Anna",                 # action verb, kein Status
        "What's the weather?",                         # status-like, aber nicht 'the status'
    ],
)
def test_negative_no_match(phrase: str) -> None:
    m = match_mission_command(phrase)
    assert m is None, f"unerwarteter Match fuer: {phrase!r} -> {m!r}"


# ---------------------------------------------------------------------------
# Cancel hat Prioritaet vor Status bei Doppel-Match
# ---------------------------------------------------------------------------

def test_cancel_wins_over_status() -> None:
    """Wenn beide Patterns matchen wuerden, gewinnt Cancel — explizite
    Stop-Anweisung darf nicht zu einem Status-Read degradiert werden."""
    m = match_mission_command("Stop OpenClaw, wie weit bist du eigentlich?")
    assert m is not None
    assert m.intent == "cancel"


# ---------------------------------------------------------------------------
# Mission-ID-Extraktion
# ---------------------------------------------------------------------------

def test_extract_uuid_v7() -> None:
    m = match_mission_command(
        "Status der Mission 0190f7a3-1234-7abc-9def-0123456789ab"
    )
    assert m is not None
    assert m.intent == "status"
    assert m.mission_id == "0190f7a3-1234-7abc-9def-0123456789ab"


def test_extract_uuid_normalized() -> None:
    """UUID ohne Bindestriche wird auf das Standardformat gemapped."""
    m = match_mission_command(
        "stop mission 0190f7a312347abc9def0123456789ab"
    )
    assert m is not None
    assert m.intent == "cancel"
    assert m.mission_id == "0190f7a3-1234-7abc-9def-0123456789ab"


def test_extract_short_alias() -> None:
    """Kurze Aliase ('mission build-foo') werden roh weitergereicht."""
    m = match_mission_command("Status der Mission build-foo")
    assert m is not None
    assert m.mission_id == "build-foo"


def test_no_mission_id_means_all_active() -> None:
    """Ohne Mission-Hinweis ist mission_id None — Caller filtert auf alle aktiven."""
    m = match_mission_command("Läuft das noch?")
    assert m is not None
    assert m.mission_id is None


# ---------------------------------------------------------------------------
# Frozen-Dataclass-Vertrag
# ---------------------------------------------------------------------------

def test_match_is_frozen() -> None:
    m = MissionCommandMatch(intent="status")
    with pytest.raises(Exception):
        m.intent = "cancel"  # type: ignore[misc]
