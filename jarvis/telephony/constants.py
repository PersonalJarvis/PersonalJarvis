"""Single source of truth for telephony call-status values (AD-T7).

Why this module exists
======================

This is the same five-layer anti-drift pattern used by
``jarvis/sessions/constants.py`` (see ``docs/anti-drift-three-layer.md``).
A call status crosses five layers:

    Python (this tuple)
      -> Pydantic model (asserted in ``status.py`` against ``CALL_STATUSES``)
      -> JSON wire format (``GET /api/telephony/calls``)
      -> TypeScript union (``frontend/src/store/events.ts``)
      -> UI label (``TelephonyView.tsx``)

BUG-008 recurred four times because this scaffolding was missing for
``hangup_reason``. We apply it preemptively here so a new status value can
never be added in one layer without surfacing it to the others. The parity
test (``tests/unit/telephony/test_constants_parity.py``) reads
``CALL_STATUSES`` and compares against the TS layer.

If you find yourself wanting a new call status: add the constant here first,
mirror it in ``store/events.ts`` (UI agent owns that file), then use the
symbol — never write a raw string at a call site.
"""

from __future__ import annotations

from typing import Final

CALL_RINGING: Final[str] = "ringing"
"""Inbound call accepted; the Media Streams socket has not delivered the
``start`` event yet (the call is being set up)."""

CALL_IN_PROGRESS: Final[str] = "in_progress"
"""Audio is flowing both ways; the per-call STT -> Brain -> TTS turn loop is
active."""

CALL_COMPLETED: Final[str] = "completed"
"""The call ended normally: the caller hung up, said a hangup phrase, or the
``max_call_seconds`` cap fired and we closed gracefully."""

CALL_FAILED: Final[str] = "failed"
"""The call ended on an unrecoverable error (bad WS secret, transcode crash,
brain failure with no spoken recovery)."""

CALL_NO_AUDIO: Final[str] = "no_audio"
"""The socket connected but no usable inbound audio frames ever arrived (the
common Twilio-misconfiguration signal — wrong codec or a dead media leg)."""

CALL_STATUSES: Final[tuple[str, ...]] = (
    CALL_RINGING,
    CALL_IN_PROGRESS,
    CALL_COMPLETED,
    CALL_FAILED,
    CALL_NO_AUDIO,
)
"""All accepted ``CallStatus`` values. Order is stable for tests that assert
against ``typing.get_args(CallStatusLiteral)`` and the TS parity check."""

# Type alias usable as a Pydantic field annotation. Kept in sync with
# CALL_STATUSES at import time by status.py's runtime assertion.
from typing import Literal  # noqa: E402

CallStatusLiteral = Literal["ringing", "in_progress", "completed", "failed", "no_audio"]

__all__ = [
    "CALL_COMPLETED",
    "CALL_FAILED",
    "CALL_IN_PROGRESS",
    "CALL_NO_AUDIO",
    "CALL_RINGING",
    "CALL_STATUSES",
    "CallStatusLiteral",
]
