"""The JarvisBar initial-visibility contract.

A persistent bar always maps when its Tk mainloop starts. Only the
non-persistent, wake-triggered variant starts withdrawn. There is deliberately
no voice-readiness override that can hide a persistent bar at boot.
"""
from __future__ import annotations

import inspect

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


def test_persistent_bar_does_not_start_withdrawn() -> None:
    bar = JarvisBarOverlay(persistent=True)
    assert bar._should_start_withdrawn() is False


def test_non_persistent_bar_starts_withdrawn() -> None:
    bar = JarvisBarOverlay(persistent=False)
    assert bar._should_start_withdrawn() is True


def test_start_hidden_override_is_not_part_of_the_surface_api() -> None:
    assert "start_hidden" not in inspect.signature(JarvisBarOverlay).parameters
