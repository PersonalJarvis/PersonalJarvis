"""Pydantic-Models fuer Voice-Session-Recording.

Drei Schichten 1:1 zum SQLite-Schema (schema.sql):

- ``VoiceSessionRow``  - Header-Row pro Session.
- ``VoiceTurnRow``     - Aggregat pro Turn (User+Jarvis).
- ``VoiceEventRow``    - Roh-Event aus dem Bus, Detail-Replay.

Plus Composite-DTOs fuer die REST-API (``SessionListItem``,
``SessionDetail``).

Konvention: ``_ms`` = Wall-Clock-Timestamp in Millisekunden seit Epoch
(passt zu existierenden Phase-6-Mission-Events). Frontend rechnet das
selbst zu ISO-Strings um — Backend bleibt bei der numerischen Form.
"""
from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from jarvis.sessions.constants import SPOKEN_KINDS

# BUG-008 (drei Episoden 2026-05-03 / -05 / -10): Pydantic-``Literal`` brach
# die List-Sessions-API jedes Mal, wenn die Pipeline einen neuen
# ``hangup_reason``-String einfuehrte (zuletzt ``turn_complete`` aus
# pipeline.py:1391). Daher: kein ``Literal`` mehr, sondern offener ``str``
# plus eine **dokumentierende** Konstante mit den heute bekannten Werten.
# Drift-Detection passiert im Test (siehe ``tests/unit/sessions/
# test_models_db_drift.py``), nicht im Pydantic-Validator — sonst kollabiert
# die UI bei jedem neuen Wert.
KNOWN_HANGUP_REASONS: frozenset[str] = frozenset(
    {
        "",  # Session laeuft noch (DB-Default solange ``ended_ms IS NULL``)
        "voice_pattern",  # User-Voice-Hangup ("Tschuess Jarvis")
        "hotkey",  # User-Hotkey-Hangup
        "idle_timeout",  # Auto-Hangup nach Inaktivitaet
        "shutdown",  # App-Shutdown beendet laufende Session
        "error",  # Pipeline-Crash
        "turn_complete",  # Normaler Turn-Ende-Pfad (pipeline.py:1391)
    }
)
"""Bekannte ``VoiceSessionEnded.hangup_reason``-Werte. Erweitern wenn die
Speech-Pipeline einen neuen Wert einfuehrt — Tests fangen Drift, nicht
Pydantic. Siehe ADR-0009 / BUGS.md BUG-008 fuer Historie."""

HangupReason: TypeAlias = str
"""Korrespondiert zu ``VoiceSessionEnded.hangup_reason`` (events.py).
Bewusst ``str`` statt ``Literal`` — siehe ``KNOWN_HANGUP_REASONS``."""


KNOWN_VOICE_TIERS: frozenset[str] = frozenset(
    {
        "",  # Kein Tier-Hinweis (z.B. Smalltalk-Fallback ohne BrainTurnStarted)
        "router",
        "openclaw",
        "sub_jarvis",  # Legacy bis Welle-4-Loeschung
        "trivial",
        "fast",
        "deep",
        "code",
    }
)
"""Routing-Tier wie in CLAUDE.md `Brain-Routing` und `Router-Discipline`."""

VoiceTier: TypeAlias = str
"""Bewusst ``str`` statt ``Literal`` — siehe ``KNOWN_VOICE_TIERS``."""


KNOWN_SPOKEN_KINDS: frozenset[str] = frozenset(SPOKEN_KINDS)
"""Bekannte ``SpeechSpoken.spoken_kind``-Werte (timeout / announcement /
clarify / …). Mirror of ``constants.SPOKEN_KINDS`` — the value rides in the
``voice_events`` payload JSON, not a typed column, so an unknown kind degrades
to a fallback UI label instead of an HTTP 500 (BUG-008 class). Parity guard:
``tests/unit/sessions/test_spoken_kind_parity.py``."""


class VoiceEventRow(BaseModel):
    """Ein Roh-Event aus dem Bus, einer Session/Turn zugeordnet."""

    model_config = ConfigDict(extra="ignore")

    seq: int | None = None
    session_id: str
    turn_id: str | None = None
    ts_ms: int
    kind: str = Field(description="Event-Typ-Name (z.B. 'TranscriptFinal').")
    payload: dict[str, object] = Field(default_factory=dict)


class VoiceTurnRow(BaseModel):
    """Aggregat eines einzelnen Voice-Turns."""

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
    # Aufgeschluesselte Latenzen (vom Recorder via SystemStateChanged-Boundaries):
    # think_ms = wie lang Jarvis "nachgedacht" hat (User-Done -> Jarvis-spricht).
    # speak_ms = wie lang Jarvis gesprochen hat (TTS-Playback-Dauer).
    think_ms: int = 0
    speak_ms: int = 0
    tool_calls: list[str] = Field(default_factory=list)
    # True when the turn ended on a two-turn voice/chat confirmation
    # (finish_reason="voice_confirm_pending"): the reply is a pending yes/no
    # question, not a normal answer, so the transcript labels it distinctly.
    awaiting_confirmation: bool = False


class VoiceSessionRow(BaseModel):
    """Header einer Voice-Session."""

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


class SessionListItem(VoiceSessionRow):
    """Listen-Eintrag mit Anzeige-freundlichen Derivaten.

    ``duration_s`` wird aus ``started_ms``/``ended_ms`` berechnet (None
    fuer noch laufende Sessions). ``preview`` ist die erste User-Utterance
    der Session (truncated), damit die UI ohne Detail-Fetch eine Zeile
    pro Session zeigen kann.
    """

    duration_s: float | None = None
    preview: str = ""


class SessionDetail(BaseModel):
    """Vollstaendige Session-Sicht: Header + Turns + alle Events."""

    model_config = ConfigDict(extra="ignore")

    session: VoiceSessionRow
    turns: list[VoiceTurnRow] = Field(default_factory=list)
    events: list[VoiceEventRow] = Field(default_factory=list)


__all__ = [
    "HangupReason",
    "KNOWN_HANGUP_REASONS",
    "KNOWN_SPOKEN_KINDS",
    "KNOWN_VOICE_TIERS",
    "SessionDetail",
    "SessionListItem",
    "VoiceEventRow",
    "VoiceSessionRow",
    "VoiceTier",
    "VoiceTurnRow",
]
