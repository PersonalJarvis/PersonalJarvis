"""Jarvis-bar overlay package — the slim default on-screen representation.

``JarvisBarOverlay`` is imported lazily so this package stays importable on a
headless host (no tkinter) for capability probing and tests.
"""
from __future__ import annotations

__all__ = ["JarvisBarOverlay", "NullOverlay"]


def __getattr__(name: str):  # lazy: avoid importing tkinter on headless import
    if name == "JarvisBarOverlay":
        from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay

        return JarvisBarOverlay
    if name == "NullOverlay":
        from jarvis.ui.jarvisbar.null_overlay import NullOverlay

        return NullOverlay
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
