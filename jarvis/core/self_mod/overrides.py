"""Curated overrides for the introspected mutable set (Wave 1.1).

The schema introspector derives every leaf path automatically, but it cannot
know whether a change is reversible, hot-reloadable, or value-sensitive — those
are human judgements. This table supplies them for the paths that matter most
(the original 13 hand-curated entries migrate here verbatim). Every other leaf
gets the safe defaults from :class:`SpecOverride` (``risk_tier="ask"``,
``needs_restart=True``).

``risk_tier`` only steers REST/CLI (which keep the SAFE auto-apply / ASK confirm
split); the voice path applies everything immediately (Wave 1.3). ``needs_restart``
drives the honest "...restart once" readback on all paths.
"""
from __future__ import annotations

from .schema_introspect import SpecOverride

OVERRIDES: dict[str, SpecOverride] = {
    "tts.provider": SpecOverride(
        risk_tier="ask", needs_restart=False,
        description="TTS provider (hot-reload covered).",
    ),
    "tts.voice_de": SpecOverride(
        risk_tier="ask", needs_restart=False,
        description="German TTS voice (hot-reload covered).",
    ),
    "tts.voice_en": SpecOverride(
        risk_tier="ask", needs_restart=False,
        description="English TTS voice (hot-reload covered).",
    ),
    "tts.speed": SpecOverride(
        risk_tier="safe", needs_restart=False,
        description="TTS speech speed (trivial, bypass-whitelisted).",
    ),
    "stt.provider": SpecOverride(
        risk_tier="ask", needs_restart=True,
        description="STT provider (STT init is not hot-reloadable).",
    ),
    "brain.primary": SpecOverride(
        risk_tier="ask", needs_restart=True,
        description="Primary brain provider (requires a BrainManager re-init).",
    ),
    "ui.theme": SpecOverride(
        risk_tier="safe", needs_restart=False,
        # Undeclared extra="allow" key on UIConfig — force-include it.
        pydantic_model_name="UIConfig", field_name="theme",
        description="UI theme (trivial, bypass-whitelisted).",
    ),
    "ui.language": SpecOverride(
        risk_tier="safe", needs_restart=False,
        description=(
            "Interface / display language of the whole app (en/de/es) — what "
            "the user SEES. Applies live, no restart."
        ),
    ),
    "profile.language": SpecOverride(
        risk_tier="ask", needs_restart=False,
        description="Profile language (legacy; canonical is brain.reply_language).",
    ),
    "brain.reply_language": SpecOverride(
        risk_tier="safe", needs_restart=False,
        description=(
            "Reply language for spoken/chat output (auto/de/en/es). Canonical "
            "language setting; applies to the next turn (no restart)."
        ),
    ),
    "stt.language": SpecOverride(
        risk_tier="ask", needs_restart=True,
        description=(
            "Speech-to-text input locale hint (auto/de/en/...). Read at STT "
            "init — needs restart to take effect."
        ),
    ),
    "tts.language_code": SpecOverride(
        risk_tier="ask", needs_restart=True,
        description=(
            "Text-to-speech output locale (de-DE/en-US/...). Read at TTS init "
            "— needs restart to take effect."
        ),
    ),
    "computer_use.step_budget": SpecOverride(
        risk_tier="ask", needs_restart=False,
        description=(
            "Computer-Use per-mission step ceiling (range 1-1000). Hot-reload "
            "— applies to the next mission."
        ),
    ),
}

__all__ = ["OVERRIDES"]
