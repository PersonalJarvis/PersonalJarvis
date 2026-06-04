"""Mascot orb right-click → show-window gesture.

Parallels the whisper-bar: the orb exposes ``set_on_show_window`` (injected by
OrbBusBridge) and ``_on_right_click`` fires it. Per the 2026-06-02 spec the
right-click now raises the main window instead of opening the old Reset/Mute
context menu; "Reset position" moves to middle-click (``<Button-2>``), and the
double-click mute gesture is unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

try:  # noqa: SIM105 — intentional try-import for the discovery quirk
    from ui.orb.overlay import OrbOverlay  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "ui.orb not on the pytest pythonpath — top-level namespace package. "
        "Run with `python -m pytest tests/unit/ui/...` from the repo root.",
        allow_module_level=True,
    )


def test_orb_set_on_show_window_stores_callback() -> None:
    orb = OrbOverlay()
    cb = lambda: None  # noqa: E731
    orb.set_on_show_window(cb)
    assert orb._on_show_window is cb  # noqa: SLF001


def test_orb_right_click_fires_show_window_callback() -> None:
    orb = OrbOverlay()
    fired: list[bool] = []
    orb.set_on_show_window(lambda: fired.append(True))

    orb._on_right_click(None)  # noqa: SLF001 — the Tk <Button-3> handler

    assert fired == [True]


def test_orb_right_click_safe_without_callback() -> None:
    orb = OrbOverlay()
    # No callback wired → silent no-op, never raises.
    orb._on_right_click(None)  # noqa: SLF001
