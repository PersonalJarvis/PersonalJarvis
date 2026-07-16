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
"""User said a hangup phrase ('auflegen', 'tschüss', 'bye') or it was i18n-allow
inferred from a closing intent."""

HANGUP_HOTKEY: Final[str] = "hotkey"
"""User pressed the global hangup hotkey."""

HANGUP_CLIENT_STOP: Final[str] = "client_stop"
"""The browser client sent an explicit audio-stop control."""

HANGUP_WS_CLOSED: Final[str] = "ws_closed"
"""The browser voice WebSocket closed without a more specific reason."""

HANGUP_REALTIME_FALLBACK: Final[str] = "realtime_fallback"
"""The browser switched from realtime voice to the classic pipeline."""

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
    HANGUP_CLIENT_STOP,
    HANGUP_WS_CLOSED,
    HANGUP_REALTIME_FALLBACK,
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

# ----------------------------------------------------------------------
# Voice-session engine mode
# ----------------------------------------------------------------------

VOICE_MODE_UNKNOWN: Final[str] = "unknown"
"""No persisted runtime evidence identifies the session's voice engine."""

VOICE_MODE_PIPELINE: Final[str] = "pipeline"
"""Classic STT -> Brain -> TTS voice pipeline."""

VOICE_MODE_REALTIME: Final[str] = "realtime"
"""Duplex realtime-provider voice session."""

VOICE_MODES: Final[tuple[str, ...]] = (
    VOICE_MODE_UNKNOWN,
    VOICE_MODE_PIPELINE,
    VOICE_MODE_REALTIME,
)
"""Canonical voice-mode values. Persistence remains open to future strings."""

# ----------------------------------------------------------------------
# Spoken-track kinds (SpeechSpoken.spoken_kind)
# ----------------------------------------------------------------------
#
# Same anti-drift contract as HANGUP_REASONS: every phrase Jarvis commits to
# the audible output path is published as a ``SpeechSpoken`` event and
# tagged with one of these kinds. The Transcription view renders a per-kind
# label, so the set has to agree across Python (this module), the Pydantic
# mirror (``models.py``), the TS const (``types.ts``) and the UI label map
# (``TurnCard.tsx``). The parity test in
# ``tests/unit/sessions/test_spoken_kind_parity.py`` fails on any drift.
#
SPOKEN_KIND_REPLY: Final[str] = "reply"
"""A normal assistant reply confirmed by the audible output path."""

SPOKEN_KIND_CLARIFY: Final[str] = "clarify"
"""A clarifying question ('Wie meinst du das genau?') for an empty/incomplete turn."""

SPOKEN_KIND_TIMEOUT: Final[str] = "timeout"
"""The brain-took-too-long apology (no-first-frame ceiling / stall window)."""

SPOKEN_KIND_UNAVAILABLE: Final[str] = "unavailable"
"""The whole brain provider chain was unreachable."""

SPOKEN_KIND_STT_UNAVAILABLE: Final[str] = "stt_unavailable"
"""Speech-to-text exhausted its retries — Jarvis could not hear the user."""

SPOKEN_KIND_PRIVACY: Final[str] = "privacy"
"""A privacy acknowledgement ('Ja.' / 'Ich sehe wieder.') for the vision toggle."""

SPOKEN_KIND_COMPLETION: Final[str] = "completion"
"""A background mission / OpenClaw result read back after it finished."""

SPOKEN_KIND_SUBAGENT: Final[str] = "subagent"
"""A spawned sub-agent / mission / worker result read back to the user. The
attributed sibling of ``completion`` — same punch-through + afterglow handling,
but rendered as its own 'Jarvis Sub-Agent / Output' track so it reads distinctly
from a generic background completion or a normal inline reply."""

SPOKEN_KIND_ACTION_DONE: Final[str] = "action_done"
"""A short 'done' confirmation after a wordless desktop action succeeded."""

SPOKEN_KIND_BACKCHANNEL: Final[str] = "backchannel"
"""A short conversational cue ('Mhm?') mid-dialogue."""

SPOKEN_KIND_ANNOUNCEMENT: Final[str] = "announcement"
"""A generic interstitial announcement (skill output, spawn ack, cron skill)."""

SPOKEN_KIND_PREAMBLE: Final[str] = "preamble"
"""The sub-second flash-brain 'let me check…' preamble before the deep reply."""

SPOKEN_KIND_PROGRESS: Final[str] = "progress"
"""A 'still working' nudge while a long background mission runs."""

SPOKEN_KIND_WITHHELD: Final[str] = "withheld"
"""The safety scrubber cancelled an unsafe answer mid-output; this is the
spoken fallback line. Recording it keeps the transcript honest about WHY the
audible answer stopped (BUG-056: the 15:13 session showed a truncated reply
with no trace of the abort)."""

SPOKEN_KIND_OTHER: Final[str] = "other"
"""Catch-all for any voiced phrase without a more specific tag."""

SPOKEN_KINDS: Final[tuple[str, ...]] = (
    SPOKEN_KIND_REPLY,
    SPOKEN_KIND_CLARIFY,
    SPOKEN_KIND_TIMEOUT,
    SPOKEN_KIND_UNAVAILABLE,
    SPOKEN_KIND_STT_UNAVAILABLE,
    SPOKEN_KIND_PRIVACY,
    SPOKEN_KIND_COMPLETION,
    SPOKEN_KIND_SUBAGENT,
    SPOKEN_KIND_ACTION_DONE,
    SPOKEN_KIND_BACKCHANNEL,
    SPOKEN_KIND_ANNOUNCEMENT,
    SPOKEN_KIND_PREAMBLE,
    SPOKEN_KIND_PROGRESS,
    SPOKEN_KIND_WITHHELD,
    SPOKEN_KIND_OTHER,
)
"""All accepted ``SpeechSpoken.spoken_kind`` values. Order is stable for tests."""


__all__ = [
    "HANGUP_CLIENT_STOP",
    "HANGUP_ERROR",
    "HANGUP_HOTKEY",
    "HANGUP_IDLE_TIMEOUT",
    "HANGUP_REALTIME_FALLBACK",
    "HANGUP_REASONS",
    "HANGUP_SHUTDOWN",
    "HANGUP_TURN_COMPLETE",
    "HANGUP_VOICE_PATTERN",
    "HANGUP_WS_CLOSED",
    "SPOKEN_KIND_ACTION_DONE",
    "SPOKEN_KIND_ANNOUNCEMENT",
    "SPOKEN_KIND_BACKCHANNEL",
    "SPOKEN_KIND_CLARIFY",
    "SPOKEN_KIND_COMPLETION",
    "SPOKEN_KIND_OTHER",
    "SPOKEN_KIND_PREAMBLE",
    "SPOKEN_KIND_PRIVACY",
    "SPOKEN_KIND_PROGRESS",
    "SPOKEN_KIND_REPLY",
    "SPOKEN_KIND_STT_UNAVAILABLE",
    "SPOKEN_KIND_SUBAGENT",
    "SPOKEN_KIND_TIMEOUT",
    "SPOKEN_KIND_UNAVAILABLE",
    "SPOKEN_KIND_WITHHELD",
    "SPOKEN_KINDS",
    "VOICE_MODES",
    "VOICE_MODE_PIPELINE",
    "VOICE_MODE_REALTIME",
    "VOICE_MODE_UNKNOWN",
]
