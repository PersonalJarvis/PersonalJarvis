"""BUG-008 drift detector: Pydantic models must accept any string as
``hangup_reason``/``tier``, otherwise ``GET /api/sessions`` collapses
as soon as the speech pipeline introduces a new value.

This bug has already had three episodes (2026-05-03 / -05 / -10). The
permanent fix lives in the module docstring of ``jarvis/sessions/models.py``.
These tests guard the fix against regressions — anyone who migrates
``HangupReason`` or ``VoiceTier`` back to ``Literal`` fails here.
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


# --- Layer 1: every known value validates -------------------------


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


# --- Layer 2: unknown values do NOT crash (drift resilience) ------


def test_unknown_hangup_reason_does_not_break_validation() -> None:
    """If the pipeline introduces ``vad_silence_long`` tomorrow, the
    sessions API must not 500. That was exactly the BUG-008 trap."""
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


# --- Layer 3: live-DB drift detector --------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_DB = _REPO_ROOT / "data" / "sessions.db"


@pytest.mark.skipif(
    not _LIVE_DB.exists(),
    reason="data/sessions.db not present (CI / fresh checkouts)",
)
def test_live_db_distinct_hangup_reasons_all_validate() -> None:
    """Crawls the real ``data/sessions.db`` and makes sure every
    ``hangup_reason`` value that occurs in practice is accepted by
    Pydantic. This is the sharp drift detector — if this test goes
    red, a new value was introduced in the pipeline without anyone
    updating ``KNOWN_HANGUP_REASONS``. Then: add the value to the
    constant + extend ``hangupLabel`` in the frontend
    (jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx).
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
        f"New hangup_reason values in data/sessions.db: {sorted(unknown)}. "
        f"Please add them to KNOWN_HANGUP_REASONS in jarvis/sessions/models.py "
        f"and to the frontend (types.ts + SessionList.hangupLabel)."
    )

    # Plus: every value MUST be Pydantic-validatable. This protects
    # against refactor regressions back to ``Literal``.
    for reason in seen:
        VoiceSessionRow(
            id="probe",
            started_ms=0,
            hangup_reason=reason,
        )
