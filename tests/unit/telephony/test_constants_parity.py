"""Five-layer enum parity guard for CallStatus (AD-T7).

Mirrors ``tests/unit/sessions/test_hangup_reason_parity.py``: the Python source
of truth (``CALL_STATUSES``) must match the ``CallStatusLiteral`` annotation,
and — when the UI agent has wired ``store/events.ts`` — the TS layer too. The
TS check is soft (skips if the literal is not present yet) so the backend suite
stays green before the frontend lands, but still catches drift once it does.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

from jarvis.telephony.constants import CALL_STATUSES, CallStatusLiteral

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVENTS_TS = _REPO_ROOT / "jarvis" / "ui" / "web" / "frontend" / "src" / "store" / "events.ts"


def test_literal_matches_source_tuple():
    literal_values = set(typing.get_args(CallStatusLiteral))
    assert literal_values == set(CALL_STATUSES)


def test_no_empty_string_in_call_statuses():
    # Unlike hangup_reason, a call always has a concrete status.
    assert "" not in CALL_STATUSES


def test_expected_values_present():
    assert set(CALL_STATUSES) == {
        "ringing",
        "in_progress",
        "completed",
        "failed",
        "no_audio",
    }


def test_ts_layer_parity_when_present():
    """If the UI agent has declared a CallStatus union in events.ts, it must
    list exactly the Python values."""
    if not _EVENTS_TS.exists():
        return  # frontend not in this checkout
    text = _EVENTS_TS.read_text(encoding="utf-8")
    match = re.search(r"CallStatus\s*=\s*([^;]+);", text)
    if not match:
        return  # UI agent has not added the union yet — soft pass
    ts_values = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert ts_values == set(CALL_STATUSES), (
        f"TS CallStatus {ts_values} drifted from Python {set(CALL_STATUSES)}"
    )
