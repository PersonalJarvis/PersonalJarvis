"""The brand palette every server-rendered page shares.

Extracted from :mod:`jarvis.ui.web.mission_graph`, which used to own it, once a
second renderer needed the same colours. A palette copied into two modules
drifts within a release: someone adjusts the yellow in one place, and the
mission map and the visualisation stop looking like they came from the same
product. One definition, imported by both.

Mirrors ``video/src/intro/theme.ts`` COLORS, which mirrors the desktop app's
``frontend/src/index.css`` dark tokens. Change all three together.

The app is dark-first and these pages open from inside it, so they commit to
the dark brand look rather than following the OS light/dark preference — the
same decision ``artifact_view`` documents for the markdown view.
"""

from __future__ import annotations

from collections.abc import Mapping

BRAND: Mapping[str, str] = {
    "bg": "#0A0A0A",
    "bg_elevated": "#141414",
    "bg_card": "#181818",
    "primary": "#FFD60A",
    "primary_deep": "#E6BE00",
    "text": "#FAFAFA",
    "text_muted": "#9A9A9A",
    "text_faint": "#6B6B6B",
    "border": "rgba(255,255,255,0.10)",
    "border_strong": "rgba(255,255,255,0.18)",
    "good": "#4ADE80",
    "primary_glow": "rgba(255,214,10,0.35)",
}

__all__ = ["BRAND"]
