"""The ``start_hidden`` capability of the JarvisBar overlay.

A persistent bar normally maps its Tk window the moment the mainloop runs (no
``withdraw``); ``start_hidden=True`` lets a caller opt it into starting
withdrawn instead. NOTE: the boot wiring no longer uses ``start_hidden`` for the
persistent bar — the bar is visible immediately at boot, decoupled from the
voice/wake path (see ``tests/unit/ui/jarvisbar/test_boot_visibility.py``). These
tests pin the overlay's own start-withdrawn decision so the boot path stays
headless-testable (no real Tk window needed).
"""
from __future__ import annotations

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


def test_persistent_bar_with_start_hidden_starts_withdrawn() -> None:
    bar = JarvisBarOverlay(persistent=True, start_hidden=True)
    assert bar._start_hidden is True
    assert bar._should_start_withdrawn() is True


def test_persistent_bar_default_does_not_start_withdrawn() -> None:
    # Backward compatibility: without start_hidden a persistent bar is mapped
    # immediately, exactly as before (e.g. the live swap / set_bar_persistent
    # paths that explicitly show it after construction).
    bar = JarvisBarOverlay(persistent=True)
    assert bar._start_hidden is False
    assert bar._should_start_withdrawn() is False


def test_non_persistent_bar_starts_withdrawn_regardless() -> None:
    # The non-persistent bar already starts hidden (pops on a session); the new
    # flag must not change that.
    bar = JarvisBarOverlay(persistent=False)
    assert bar._should_start_withdrawn() is True
