"""Phase-6 Voice-Layer fuer Mission-Status-Readback.

Re-Exports der Public-API.

Foundation: ADR-0009 §"Offen" — Voice-Readback bei MissionApproved/Failed
nutzt PhrasePicker-Pattern (anti-repeat-window). Tone aus
`jarvis/brain/JARVIS_PERSONA.md`: butler register, never "Sir". The
mission-status templates are name-neutral (they carry no hardcoded owner
name) so a fresh clone never speaks the maintainer's name.
"""
from __future__ import annotations

from .listener import MissionVoiceListener
from .readback import (
    MAX_VOICE_CHARS,
    MissionReadback,
    READBACK_TEMPLATES,
)

__all__ = [
    "MAX_VOICE_CHARS",
    "MissionReadback",
    "MissionVoiceListener",
    "READBACK_TEMPLATES",
]
