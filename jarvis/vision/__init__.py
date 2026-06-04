"""Vision-Engine (Phase 5 Capability 1).

Exports:
    - ScreenshotSource — Primary-Monitor-Screenshot via mss.
    - UIATreeSource — gepruneder UIA-Tree via pywinauto.
    - VisionEngine — orchestrierter Entry-Point mit Heuristik + Cache.
    - VisionCache — Hash-basierter Observation-Cache.
    - Pruning-Helpers (pruning.py) — reine Funktionen, testbar ohne Windows.

Siehe ADR-0002 fuer die Pruning-Strategie und
`jarvis.core.protocols.VisionSource` fuer den Contract.
"""
from __future__ import annotations

from .cache import VisionCache
from .engine import VisionEngine
from .screenshot import ScreenshotSource
from .uia_tree import UIATreeSource

__all__ = [
    "ScreenshotSource",
    "UIATreeSource",
    "VisionCache",
    "VisionEngine",
]
