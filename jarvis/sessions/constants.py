"""Single source of truth for voice-session enum-like values.

Why this module exists
======================

BUG-008 occurred twice (2026-05-03, 2026-05-05). Each time the root cause
was the same: a hangup_reason string was added to the runtime path
(``speech/pipeline.py`` or ``sessions/init.py``) without also being added
to the Pydantic ``HangupReason`` Literal in ``models.py``. The first
list-API request that returned a session with the new value validated
against the Literal, raised ``ValidationError``, FastAPI converted that
to HTTP 500, and the UI showed an empty state.

By exporting a single tuple here and importing symbolic constants at
every call site, three things become impossible:

1. A typo at the call site (``"turn_complte"`` would pass the Literal
   check today; it cannot pass an attribute lookup).
2. Adding a runtime value without surfacing it to readers of this
   module — the tuple is the contract.
3. Asymmetric drift between Python and TypeScript — the parity test
   in ``tests/unit/sessions/test_hangup_reason_parity.py`` reads
   ``HANGUP_REASONS`` and compares against the TS / TSX / SQL layers.

If you find yourself wanting to write a new hangup-reason string at a
call site: add the constant here first, regenerate the Literal in
``models.py`` (manual mirror — see comment there), then use the symbol.
"""
from __future__ import annotations

from typing import Final

HANGUP_VOICE_PATTERN: Final[str] = "voice_pattern"
"""User said a hangup phrase ('auflegen', 'tschüss', 'bye') or it was
inferred from a closing intent."""

HANGUP_HOTKEY: Final[str] = "hotkey"
"""User pressed the global hangup hotkey."""

HANGUP_IDLE_TIMEOUT: Final[str] = "idle_timeout"
"""No further user speech within the idle window after the last turn."""

HANGUP_TURN_COMPLETE: Final[str] = "turn_complete"
"""Pipeline closed the session right after the turn finished — used
for one-shot interactions where staying open would only waste mic
power. Set explicitly by the pipeline; do not infer from silence
(that is ``idle_timeout``)."""

HANGUP_SHUTDOWN: Final[str] = "shutdown"
"""Process is exiting (graceful shutdown or crash-recovery sweep at
startup that finalizes leftover sessions from a hard-kill)."""

HANGUP_ERROR: Final[str] = "error"
"""An unrecoverable exception inside the run loop forced session
termination. The exact failure should be in the trace logs; this
string only marks the session row."""

HANGUP_REASONS: Final[tuple[str, ...]] = (
    HANGUP_VOICE_PATTERN,
    HANGUP_HOTKEY,
    HANGUP_IDLE_TIMEOUT,
    HANGUP_TURN_COMPLETE,
    HANGUP_SHUTDOWN,
    HANGUP_ERROR,
    "",
)
"""All accepted ``hangup_reason`` values. The empty string represents a
session row whose recorder has not finalized yet (still running). Order
is intentionally stable for tests that assert against
``typing.get_args(HangupReason)``."""

__all__ = [
    "HANGUP_ERROR",
    "HANGUP_HOTKEY",
    "HANGUP_IDLE_TIMEOUT",
    "HANGUP_REASONS",
    "HANGUP_SHUTDOWN",
    "HANGUP_TURN_COMPLETE",
    "HANGUP_VOICE_PATTERN",
]
