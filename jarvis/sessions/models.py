"""Pydantic models for voice-session recording.

Three layers mapping 1:1 to the SQLite schema (schema.sql):

- ``VoiceSessionRow``  - header row per session.
- ``VoiceTurnRow``     - aggregate per turn (user+Jarvis).
- ``VoiceEventRow``    - raw event from the bus, for detail replay.

Plus composite DTOs for the REST API (``SessionListItem``,
``SessionDetail``).

Convention: ``_ms`` = wall-clock timestamp in milliseconds since epoch
(matches existing Phase-6 mission events). The frontend converts this
to ISO strings itself — the backend stays with the numeric form.
"""
from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from jarvis.sessions.constants import SPOKEN_KINDS, VOICE_MODE_UNKNOWN, VOICE_MODES

# BUG-008 (three episodes 2026-05-03 / -05 / -10): the Pydantic ``Literal``
# broke the list-sessions API every time the pipeline introduced a new
# ``hangup_reason`` string (most recently ``turn_complete`` from
# pipeline.py:1391). So: no more ``Literal``, instead an open ``str``
# plus a **documenting** constant with the values known today.
# Drift detection happens in the test (see ``tests/unit/sessions/
# test_models_db_drift.py``), not in the Pydantic validator — otherwise
# the UI collapses on every new value.
KNOWN_HANGUP_REASONS: frozenset[str] = frozenset(
    {
        "",  # session still running (DB default while ``ended_ms IS NULL``)
        "voice_pattern",  # user voice hangup ("bye Jarvis")
        "hotkey",  # user hotkey hangup
        "client_stop",  # browser client explicitly stopped microphone audio
        "ws_closed",  # browser voice WebSocket closed
        "realtime_fallback",  # browser switched to the classic voice pipeline
        "idle_timeout",  # auto-hangup after inactivity
        "shutdown",  # app shutdown ends the running session
        "error",  # pipeline crash
        "turn_complete",  # normal turn-end path (pipeline.py:1391)
    }
)
"""Known ``VoiceSessionEnded.hangup_reason`` values. Extend when the
speech pipeline introduces a new value — tests catch drift, not
Pydantic. See ADR-0009 / BUGS.md BUG-008 for history."""

HangupReason: TypeAlias = str
"""Corresponds to ``VoiceSessionEnded.hangup_reason`` (events.py).
Deliberately ``str`` instead of ``Literal`` — see ``KNOWN_HANGUP_REASONS``."""


KNOWN_VOICE_TIERS: frozenset[str] = frozenset(
    {
        "",  # no tier hint (e.g. smalltalk fallback without BrainTurnStarted)
        "router",
        "openclaw",
        "sub_jarvis",  # legacy, kept until the Wave 4 removal
        "trivial",
        "fast",
        "deep",
        "code",
        "realtime",
    }
)
"""Routing tier as in CLAUDE.md `Brain-Routing` and `Router-Discipline`."""

VoiceTier: TypeAlias = str
"""Open string so a future routing tier cannot break session APIs."""


KNOWN_VOICE_MODES: frozenset[str] = frozenset(VOICE_MODES)
"""Canonical session engine modes understood by the current UI."""

VoiceMode: TypeAlias = str
"""Open string so a future runtime value cannot break session APIs."""


KNOWN_SPOKEN_KINDS: frozenset[str] = frozenset(SPOKEN_KINDS)
"""Known ``SpeechSpoken.spoken_kind`` values (timeout / announcement /
clarify / …). Mirror of ``constants.SPOKEN_KINDS`` — the value rides in the
``voice_events`` payload JSON, not a typed column, so an unknown kind degrades
to a fallback UI label instead of an HTTP 500 (BUG-008 class). Parity guard:
``tests/unit/sessions/test_spoken_kind_parity.py``."""


class VoiceEventRow(BaseModel):
    """A raw event from the bus, associated with a session/turn."""

    model_config = ConfigDict(extra="ignore")

    seq: int | None = None
    session_id: str
    turn_id: str | None = None
    ts_ms: int
    kind: str = Field(description="Event type name (e.g. 'TranscriptFinal').")
    payload: dict[str, object] = Field(default_factory=dict)


class VoiceTurnRow(BaseModel):
    """Aggregate of a single voice turn."""

    model_config = ConfigDict(extra="ignore")

    id: str
    session_id: str
    idx: int = 0
    started_ms: int
    ended_ms: int | None = None
    user_text: str = ""
    user_lang: str = "de"
    jarvis_text: str = ""
    jarvis_lang: str = "de"
    tier: VoiceTier = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_total_ms: int = 0
    # Broken-down latencies (from the recorder via SystemStateChanged boundaries):
    # think_ms = how long Jarvis "thought" (user-done -> Jarvis-speaks).
    # speak_ms = how long Jarvis spoke (TTS playback duration).
    think_ms: int = 0
    speak_ms: int = 0
    tool_calls: list[str] = Field(default_factory=list)
    # True when the turn ended on a two-turn voice/chat confirmation
    # (finish_reason="voice_confirm_pending"): the reply is a pending yes/no
    # question, not a normal answer, so the transcript labels it distinctly.
    awaiting_confirmation: bool = False
    # Which voice actually spoke the reply ("Fenrir", "Charon", an ElevenLabs
    # voice id) and the speaking family ("gemini-live", "openrouter",
    # "grok-voice"). Empty when the speaking layer could not tell — the UI
    # must show nothing rather than guess (the speaker can differ from the
    # brain provider, e.g. a surface-TTS readback inside a realtime session).
    voice_name: str = ""
    voice_provider: str = ""


class VoiceSessionRow(BaseModel):
    """Header of a voice session."""

    model_config = ConfigDict(extra="ignore")

    id: str
    started_ms: int
    ended_ms: int | None = None
    hangup_reason: HangupReason = ""
    turn_count: int = 0
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    providers_used: list[str] = Field(default_factory=list)
    language: str = "de"
    wake_keyword: str = ""
    voice_mode: VoiceMode = VOICE_MODE_UNKNOWN


class SessionListItem(VoiceSessionRow):
    """List entry with display-friendly derived fields.

    ``duration_s`` is computed from ``started_ms``/``ended_ms`` (None
    for sessions still running). ``preview`` is the session's first
    user utterance (truncated), so the UI can show one line per
    session without a detail fetch.
    """

    duration_s: float | None = None
    preview: str = ""


class SessionDetail(BaseModel):
    """Complete session view: header + turns + all events."""

    model_config = ConfigDict(extra="ignore")

    session: VoiceSessionRow
    turns: list[VoiceTurnRow] = Field(default_factory=list)
    events: list[VoiceEventRow] = Field(default_factory=list)


__all__ = [
    "HangupReason",
    "KNOWN_HANGUP_REASONS",
    "KNOWN_SPOKEN_KINDS",
    "KNOWN_VOICE_TIERS",
    "KNOWN_VOICE_MODES",
    "SessionDetail",
    "SessionListItem",
    "VoiceEventRow",
    "VoiceMode",
    "VoiceSessionRow",
    "VoiceTier",
    "VoiceTurnRow",
]
