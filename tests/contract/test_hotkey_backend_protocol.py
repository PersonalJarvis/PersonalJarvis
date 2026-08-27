"""Contract: every hotkey backend answers ``chord_is_down`` the same way.

The three-way answer is the whole contract (see ``HotkeyBackend.chord_is_down``):
``True`` / ``False`` when the backend can see the keyboard, ``None`` when it
cannot — and BEFORE it listens, every backend cannot. A consumer that reads
``None`` as "up" would invent a key release on a Wayland box or a not-yet
started listener, which is precisely the phantom this capability must never
produce (BUG-191). Constructing a backend touches no OS hook, so this runs on
every CI leg, headless included.
"""

from __future__ import annotations

import pytest

from jarvis.trigger.backends import HotkeyBackend
from tests.fakes.fake_hotkey_backend import FakeHotkeyBackend


def _backend_factories():
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend
    from jarvis.trigger.backends.noop import NoopBackend
    from jarvis.trigger.backends.pynput import PynputBackend
    from jarvis.trigger.backends.quartz import QuartzHotkeyBackend

    return [
        pytest.param(GlobalHotkeysBackend, id="windows"),
        pytest.param(QuartzHotkeyBackend, id="macos"),
        pytest.param(PynputBackend, id="linux-x11"),
        pytest.param(NoopBackend, id="noop"),
        pytest.param(FakeHotkeyBackend, id="fake"),
    ]


@pytest.mark.parametrize("factory", _backend_factories())
def test_every_backend_satisfies_the_protocol_including_the_key_state_probe(factory):
    backend = factory()
    assert isinstance(backend, HotkeyBackend)
    assert callable(getattr(backend, "chord_is_down", None))


@pytest.mark.parametrize("factory", _backend_factories())
def test_a_backend_that_is_not_listening_answers_unknown_never_up(factory):
    """Not started → ``None``. ``False`` here would be a phantom release."""
    backend = factory()
    assert backend.chord_is_down("control + alt + j") is None
    assert backend.chord_is_down("control + window") is None


@pytest.mark.parametrize("factory", _backend_factories())
def test_an_empty_combo_is_unknown_not_down(factory):
    backend = factory()
    assert backend.chord_is_down("") is None
