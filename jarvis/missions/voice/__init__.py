"""Phase-6 voice layer for mission-status readback.

Re-exports of the public API.

Foundation: ADR-0009 §"Open" — voice readback on MissionApproved/Failed
uses the PhrasePicker pattern (anti-repeat window). Tone from
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
