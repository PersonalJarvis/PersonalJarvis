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


@pytest.mark.parametrize("factory", _backend_factories())
def test_held_tokens_is_unknown_before_the_backend_listens(factory):
    """The recorder snapshot must not invent a held modifier on a host that
    is not listening yet — same three-way contract as ``chord_is_down``."""
    backend = factory()
    probe = getattr(backend, "held_tokens", None)
    assert callable(probe)
    assert probe() is None


# --- optional readiness probes (just-in-time permissions) ---------------------
#
# A backend MAY expose ``is_listening()``, ``waiting_for_permission`` and
# ``deaf_tap_suspected()``; ``HotkeyTrigger`` reads them through ``getattr``.
# Whatever it exposes must never claim a running listener, a missing permission
# or a deaf tap BEFORE ``start``: the "Esc to cancel" pill and the shortcuts
# status trust these answers.


@pytest.mark.parametrize("factory", _backend_factories())
def test_optional_probes_never_claim_a_listener_before_start(factory):
    backend = factory()
    listening = getattr(backend, "is_listening", None)
    if callable(listening):
        assert listening() is False
    assert getattr(backend, "waiting_for_permission", False) is False
    deaf = getattr(backend, "deaf_tap_suspected", None)
    if callable(deaf):
        assert deaf() is False


@pytest.mark.parametrize("factory", _backend_factories())
def test_the_trigger_reads_a_backend_without_probes_as_unknown(factory):
    """``listening()`` is ``None`` for a backend that cannot say, never ``False``."""
    from jarvis.trigger.hotkey import HotkeyTrigger

    trigger = HotkeyTrigger({"call": ["f3+f4"]})
    backend = factory()
    trigger._backend = backend
    has_probe = callable(getattr(backend, "is_listening", None))
    assert trigger.listening() is (False if has_probe else None)
    assert trigger.armed is False
    assert trigger.deaf_tap_suspected() is False


def test_only_the_macos_and_the_noop_backends_have_a_listener_probe():
    """Windows and Linux keep today's pill behaviour; the no-op backend says "never"."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend
    from jarvis.trigger.backends.noop import NoopBackend
    from jarvis.trigger.backends.pynput import PynputBackend
    from jarvis.trigger.backends.quartz import QuartzHotkeyBackend

    assert callable(QuartzHotkeyBackend().is_listening)
    # Nothing is ever armed by the no-op backend: "not listening", not "cannot say".
    assert NoopBackend().is_listening() is False
    for other in (GlobalHotkeysBackend, PynputBackend, NoopBackend):
        if other is not NoopBackend:
            assert not hasattr(other(), "is_listening")
        # Only the macOS tap can WAIT for a permission (the trigger's re-arm hook).
        assert not hasattr(other(), "waiting_for_permission")
