"""Vision engine (Phase 5 Capability 1).

Exports:
    - ScreenshotSource — primary-monitor screenshot via mss.
    - UIATreeSource — pruned UIA tree via pywinauto.
    - VisionEngine — orchestrated entry point with heuristics + cache.
    - VisionCache — hash-based observation cache.
    - Pruning helpers (pruning.py) — pure functions, testable without Windows.

See ADR-0002 for the pruning strategy and
`jarvis.core.protocols.VisionSource` for the contract.
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
