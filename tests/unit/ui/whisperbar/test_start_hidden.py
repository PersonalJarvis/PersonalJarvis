"""A persistent WhisperBar must be able to start HIDDEN at boot.

The persistent bar normally maps its Tk window the moment the mainloop runs
(no ``withdraw``), so on boot it appears seconds before the speech pipeline can
actually hear the user — the "looks ready but isn't" boot confusion. The boot
wiring opts the persistent bar into ``start_hidden=True`` and only reveals it
once voice is ready; these tests pin the start-withdrawn decision so the boot
path stays headless-testable (no real Tk window needed).
"""
from __future__ import annotations

from jarvis.ui.whisperbar.overlay import WhisperBarOverlay


def test_persistent_bar_with_start_hidden_starts_withdrawn() -> None:
    bar = WhisperBarOverlay(persistent=True, start_hidden=True)
    assert bar._start_hidden is True
    assert bar._should_start_withdrawn() is True


def test_persistent_bar_default_does_not_start_withdrawn() -> None:
    # Backward compatibility: without start_hidden a persistent bar is mapped
    # immediately, exactly as before (e.g. the live swap / set_bar_persistent
    # paths that explicitly show it after construction).
    bar = WhisperBarOverlay(persistent=True)
    assert bar._start_hidden is False
    assert bar._should_start_withdrawn() is False


def test_non_persistent_bar_starts_withdrawn_regardless() -> None:
    # The non-persistent bar already starts hidden (pops on a session); the new
    # flag must not change that.
    bar = WhisperBarOverlay(persistent=False)
    assert bar._should_start_withdrawn() is True
