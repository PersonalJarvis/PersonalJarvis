"""Screen Context — one-shot, intent-driven, privacy-filtered screen capture.

When a user turn unambiguously asks Jarvis to look ("can you see this?", "schau
dir das an", "mira esto"), this package takes exactly ONE capture of the screen
the user is working on — the monitor under the mouse cursor — enriches it with
the active app, the window title and visible UI text from the platform's
accessibility layer, filters it against the user's privacy rules, and hands it
to the running conversation behind a short-lived, single-use handle.

When the turn is only *possibly* about the screen, Jarvis asks instead of
capturing. There is no configuration in which a capture happens without a
matching request, and there is no code path that captures twice.

Design, flow, privacy plan and roadmap: ``docs/screen-context.md``.

Nothing is imported eagerly here beyond the data model: the platform ports load
their native libraries on first use, so importing this package costs nothing on
the startup path (AP-26) and stays clean on a headless container.
"""
from __future__ import annotations

from jarvis.screen_context.models import (
    CaptureTarget,
    Degradation,
    DegradationCode,
    IntentVerdict,
    RedactionReport,
    ScreenContext,
    TargetKind,
    TargetReason,
    VisualIntent,
    WindowFacts,
)

__all__ = [
    "CaptureTarget",
    "Degradation",
    "DegradationCode",
    "IntentVerdict",
    "RedactionReport",
    "ScreenContext",
    "TargetKind",
    "TargetReason",
    "VisualIntent",
    "WindowFacts",
]
