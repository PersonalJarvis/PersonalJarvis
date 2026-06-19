"""BUG-008-Drift-Detector: Pydantic-Models muessen jeden String als
``hangup_reason``/``tier`` akzeptieren, sonst kollabiert ``GET
/api/sessions`` sobald die Speech-Pipeline einen neuen Wert einfuehrt.

Drei Episoden hat dieser Bug schon gehabt (2026-05-03 / -05 / -10). Der
permanente Fix steht im Modul-Docstring von ``jarvis/sessions/models.py``.
Diese Tests sichern den Fix gegen Regressions ab — wer ``HangupReason``
oder ``VoiceTier`` zurueck auf ``Literal`` migriert, faellt hier durch.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.sessions.models import (
    KNOWN_HANGUP_REASONS,
    KNOWN_VOICE_TIERS,
    SessionListItem,
    VoiceSessionRow,
    VoiceTurnRow,
)


# --- Schicht 1: jeder bekannte Wert validiert -------------------------


@pytest.mark.parametrize("reason", sorted(KNOWN_HANGUP_REASONS))
def test_session_validates_every_known_hangup_reason(reason: str) -> None:
    item = VoiceSessionRow(
        id="s1",
        started_ms=0,
        ended_ms=100,
        hangup_reason=reason,
    )
    assert item.hangup_reason == reason


@pytest.mark.parametrize("tier", sorted(KNOWN_VOICE_TIERS))
def test_turn_validates_every_known_tier(tier: str) -> None:
    turn = VoiceTurnRow(
        id="t1",
        session_id="s1",
        started_ms=0,
        tier=tier,
    )
    assert turn.tier == tier


# --- Schicht 2: unbekannte Werte krashen NICHT (Drift-Resilienz) ------


def test_unknown_hangup_reason_does_not_break_validation() -> None:
    """Wenn die Pipeline morgen ``vad_silence_long`` einfuehrt, darf die
    Sessions-API nicht 500 werden. Das war exakt die BUG-008-Falle."""
    item = SessionListItem(
        id="s1",
        started_ms=0,
        ended_ms=100,
        hangup_reason="vad_silence_long_future_value",
        turn_count=1,
    )
    assert item.hangup_reason == "vad_silence_long_future_value"


def test_unknown_tier_does_not_break_validation() -> None:
    turn = VoiceTurnRow(
        id="t1",
        session_id="s1",
        started_ms=0,
        tier="phase8_future_tier",
    )
    assert turn.tier == "phase8_future_tier"


# --- Schicht 3: Live-DB-Drift-Detector --------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_DB = _REPO_ROOT / "data" / "sessions.db"


@pytest.mark.skipif(
    not _LIVE_DB.exists(),
    reason="data/sessions.db nicht vorhanden (CI / frische Checkouts)",
)
def test_live_db_distinct_hangup_reasons_all_validate() -> None:
    """Crawlt die echte ``data/sessions.db`` und stellt sicher, dass jeder
    in der Praxis vorkommende ``hangup_reason``-Wert von Pydantic
    akzeptiert wird. Das ist der scharfe Drift-Detector — wenn dieser
    Test rot wird, wurde ein neuer Wert in der Pipeline eingefuehrt
    ohne dass jemand ``KNOWN_HANGUP_REASONS`` aktualisiert hat. Dann:
    Wert in die Konstante eintragen + ``hangupLabel`` im Frontend
    erweitern (jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx).
    """
    con = sqlite3.connect(str(_LIVE_DB))
    try:
        rows = con.execute(
            "SELECT DISTINCT hangup_reason FROM voice_sessions"
        ).fetchall()
    finally:
        con.close()

    seen: set[str] = {(row[0] or "") for row in rows}
    unknown = seen - KNOWN_HANGUP_REASONS
    assert not unknown, (
        f"Neue hangup_reason-Werte in data/sessions.db: {sorted(unknown)}. "
        f"Bitte in jarvis/sessions/models.py KNOWN_HANGUP_REASONS und im "
        f"Frontend (types.ts + SessionList.hangupLabel) ergaenzen."
    )

    # Plus: jeder Wert MUSS Pydantic-validierbar sein. Das schuetzt
    # gegen Refactor-Regressions zurueck zu ``Literal``.
    for reason in seen:
        VoiceSessionRow(
            id="probe",
            started_ms=0,
            hangup_reason=reason,
        )
