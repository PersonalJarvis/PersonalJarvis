"""Audio ducking — "Mute music while dictating".

Lowers other apps' audio for the duration of a voice session and restores it
afterwards. Windows: per-app session mute via pycaw. macOS: AppleScript volume
duck of the known players (Music, Spotify) with an opt-in master-output
fallback. A logged no-op elsewhere so the base headless install boots
unaffected.
"""
from __future__ import annotations

__all__ = ["AudioDuckController", "make_audio_duck_controller"]


def __getattr__(name: str):  # lazy: controller imports events; keep package light
    if name in ("AudioDuckController", "make_audio_duck_controller"):
        from jarvis.audio.ducking import controller

        return getattr(controller, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
