"""Whisper-bar overlay package — the slim default on-screen representation.

``WhisperBarOverlay`` is imported lazily so this package stays importable on a
headless host (no tkinter) for capability probing and tests.
"""
from __future__ import annotations

__all__ = ["WhisperBarOverlay", "NullOverlay"]


def __getattr__(name: str):  # lazy: avoid importing tkinter on headless import
    if name == "WhisperBarOverlay":
        from jarvis.ui.whisperbar.overlay import WhisperBarOverlay

        return WhisperBarOverlay
    if name == "NullOverlay":
        from jarvis.ui.whisperbar.null_overlay import NullOverlay

        return NullOverlay
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
