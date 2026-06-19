"""DB-vs-schema compatibility check for the live sessions database.

The parity test in ``tests/unit/sessions/test_hangup_reason_parity.py``
confirms the four code-side layers agree. This test reads what is
actually *on disk* — namely, every distinct ``hangup_reason`` value
already written by past pipeline runs — and asserts each value is in
``HANGUP_REASONS``.

Why we need both
----------------

Static parity checks the four code layers; the DB check covers a
fifth surface area: code paths that bypass the constants module
entirely (e.g. a hard-coded string snuck into a script, or a value
written by an older build that nobody migrated). If a developer
introduces a new value via the runtime path without registering it
in ``constants.py``, this test will fail on any machine that has
already produced a session with the new value.

The test is *defensive*: if no DB exists yet (fresh checkout, CI
without sessions), it is skipped rather than failing — we do not
want to force every developer to seed a DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.sessions.constants import HANGUP_REASONS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "sessions.db"


def _db_path() -> Path | None:
    if DEFAULT_DB.exists():
        return DEFAULT_DB
    return None


def test_db_hangup_reasons_are_a_subset_of_constants() -> None:
    """Every hangup_reason on disk must be registered in constants.py."""
    db = _db_path()
    if db is None:
        pytest.skip("data/sessions.db not present — skipping DB compatibility check")

    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT DISTINCT hangup_reason FROM voice_sessions "
            "WHERE hangup_reason IS NOT NULL"
        )
        observed = {row[0] for row in cur.fetchall()}
    finally:
        con.close()

    unknown = observed - set(HANGUP_REASONS)
    assert not unknown, (
        f"Found hangup_reason values in {db} that are NOT registered in "
        f"jarvis/sessions/constants.HANGUP_REASONS: {sorted(unknown)!r}. "
        "Either add them to the constants tuple (and update the Pydantic "
        "Literal + TS union + TSX switch + SQL comment via the parity "
        "test), or sanitize the rows. Leaving them is BUG-008 in waiting."
    )
