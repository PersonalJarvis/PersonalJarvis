"""``chord_is_down`` — the physical key-state probe behind the hold watchdog.

BUG-191: the press/release EDGES between the OS poller and the pipeline can be
lost, and a hold-to-record lane that only hears edges then records until its
cap. The probe asks the keyboard instead. Three answers: ``True`` / ``False``
when the backend can see the keyboard, ``None`` when it cannot — and ``None``
must never be read as "up", or the watchdog would invent a release.

Windows is exercised against a fake ``GetAsyncKeyState`` (the real one needs
pywin32 and a desktop); pynput and Quartz against their own held-set; the
trigger's aggregation against ``FakeHotkeyBackend``.
"""

from __future__ import annotations

import sys

import pytest

from tests.fakes.fake_global_hotkeys import FakeGlobalHotkeys
from tests.fakes.fake_hotkey_backend import FakeHotkeyBackend


@pytest.fixture()
def fake_gh():
    """A fresh FakeGlobalHotkeys + a reset refcount, like the backend tests."""
    import jarvis.trigger.backends.global_hotkeys as ghb

    fake = FakeGlobalHotkeys()
    saved = sys.modules.get("global_hotkeys")
    sys.modules["global_hotkeys"] = fake
    ghb._reset_checker_state_for_tests()
    try:
        yield fake
    finally:
        ghb._reset_checker_state_for_tests()
        if saved is not None:
            sys.modules["global_hotkeys"] = saved
        else:
            sys.modules.pop("global_hotkeys", None)


@pytest.fixture()
def key_state(monkeypatch):
    """Fake ``GetAsyncKeyState`` + a small VK table for the Windows backend."""
    import jarvis.trigger.backends.global_hotkeys as ghb

    down: set[int] = set()
    table = {"control": 0x11, "shift": 0x10, "alt": 0x12, "j": 0x4A, "f9": 0x78}
    monkeypatch.setattr(ghb, "_virtual_key_for", lambda token: table.get(token))
    monkeypatch.setattr(ghb, "_async_key_is_down", lambda vk: int(vk) in down)
    return down


# ----------------------------------------------------------------------
# Windows
# ----------------------------------------------------------------------


def test_windows_backend_reads_the_keyboard_not_its_edges(fake_gh, key_state):
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    backend = GlobalHotkeysBackend()
    backend.register([["control + window", lambda: None, lambda: None]])
    backend.start()

    assert backend.chord_is_down("control + window") is False
    key_state.update({0x11, 0x5B})  # Ctrl + LEFT Win
    assert backend.chord_is_down("control + window") is True
    key_state.discard(0x5B)
    assert backend.chord_is_down("control + window") is False
    key_state.add(0x5C)  # Ctrl + RIGHT Win: "window" means either key
    assert backend.chord_is_down("control + window") is True
    key_state.discard(0x11)
    assert backend.chord_is_down("control + window") is False

    key_state.update({0x11, 0x12, 0x4A})
    assert backend.chord_is_down("control + alt + j") is True
    backend.stop()
    backend.unregister()


def test_windows_backend_answers_unknown_for_a_token_it_cannot_name(fake_gh, key_state):
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    backend = GlobalHotkeysBackend()
    backend.register([["control + j", lambda: None, lambda: None]])
    backend.start()
    key_state.update({0x11})
    assert backend.chord_is_down("control + nosuchkey") is None
    assert backend.chord_is_down("") is None


def test_windows_backend_answers_unknown_when_the_probe_itself_cannot(fake_gh, monkeypatch):
    """No pywin32 → the key probe is None → the answer is None, never False."""
    import jarvis.trigger.backends.global_hotkeys as ghb
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    monkeypatch.setattr(ghb, "_virtual_key_for", lambda token: 0x11)
    monkeypatch.setattr(ghb, "_async_key_is_down", lambda vk: None)
    backend = GlobalHotkeysBackend()
    backend.register([["control + j", lambda: None, lambda: None]])
    backend.start()
    assert backend.chord_is_down("control + j") is None


def test_windows_backend_that_degraded_answers_unknown(key_state):
    """No package registered (``_gh`` is None) → nothing to read from."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    key_state.update({0x11, 0x4A})
    assert GlobalHotkeysBackend().chord_is_down("control + j") is None


# ----------------------------------------------------------------------
# macOS / Linux-X11 / no-op
# ----------------------------------------------------------------------


def test_pynput_backend_answers_from_its_held_set_only_while_listening():
    from jarvis.trigger.backends.pynput import PynputBackend

    backend = PynputBackend()
    backend.register([["control + alt + j", lambda: None, lambda: None]])
    backend._held.update({"ctrl", "alt", "j"})
    assert backend.chord_is_down("control + alt + j") is None, "not listening yet"

    backend._started = True
    backend._listener = object()
    assert backend.chord_is_down("control + alt + j") is True
    backend._held.discard("j")
    assert backend.chord_is_down("control + alt + j") is False
    # A right-hand modifier satisfies the generic token, as in the matcher.
    backend._held.add("j")
    backend._held.discard("alt")
    backend._held.add("alt_r")
    assert backend.chord_is_down("control + alt + j") is True
    assert backend.chord_is_down("") is None


def test_quartz_backend_answers_from_its_held_set_only_while_tapping():
    from jarvis.trigger.backends.quartz import QuartzHotkeyBackend

    backend = QuartzHotkeyBackend()
    backend.register([["control + window", lambda: None, lambda: None]])
    backend._held.update({"ctrl", "cmd"})
    assert backend.chord_is_down("control + window") is None, "no tap yet"

    backend._started = True
    backend._tap = object()
    assert backend.chord_is_down("control + window") is True
    backend._held.discard("cmd")
    assert backend.chord_is_down("control + window") is False
    assert backend.chord_is_down("") is None


def test_noop_backend_never_pretends_to_see_the_keyboard():
    from jarvis.trigger.backends.noop import NoopBackend

    backend = NoopBackend()
    backend.register([["control + j", lambda: None, lambda: None]])
    backend.start()
    assert backend.chord_is_down("control + j") is None


# ----------------------------------------------------------------------
# The trigger asks its backend per armed combo
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chord_is_down_aggregates_every_combo_of_a_binding(monkeypatch):
    """Any combo down → True; all answered up → False; nobody can tell → None.

    A binding may be armed under two spellings (the AltGr compatibility
    chord), and the physical answer is "is ANY of them down". ``None`` is
    reserved for "unknown" and survives aggregation: one combo answering
    ``False`` and the other ``None`` is still ``False`` (something WAS seen),
    but a binding where nothing was seen stays ``None``.
    """
    import jarvis.trigger.hotkey as hk

    fake = FakeHotkeyBackend()
    monkeypatch.setattr(hk, "make_hotkey_backend", lambda: fake)
    trig = hk.HotkeyTrigger({"dictate": ["ctrl+win", "f9"]}, push_to_talk={"dictate"})
    async with trig:
        assert trig.chord_is_down("dictate") is None, "a fake with no key state is unknown"
        fake.key_state_default = False
        assert trig.chord_is_down("dictate") is False
        assert sorted(set(fake.key_state_queries)) == ["control+window", "f9"]
        fake.key_state["f9"] = True
        assert trig.chord_is_down("dictate") is True
        fake.key_state = {"control+window": False}
        fake.key_state_default = None
        assert trig.chord_is_down("dictate") is False, "one seen answer is an answer"
        assert trig.chord_is_down("no_such_binding") is None


def test_chord_is_down_without_a_backend_is_unknown():
    import jarvis.trigger.hotkey as hk

    trig = hk.HotkeyTrigger({"dictate": ["ctrl+win"]}, push_to_talk={"dictate"})
    assert trig.chord_is_down("dictate") is None  # never entered: no backend


@pytest.mark.asyncio
async def test_a_backend_without_the_probe_is_unknown_not_up(monkeypatch):
    """An older / third-party backend that lacks ``chord_is_down`` degrades to
    edges-only; it must never read as a released key."""
    import jarvis.trigger.hotkey as hk

    class _Legacy(FakeHotkeyBackend):
        chord_is_down = None  # type: ignore[assignment]

    fake = _Legacy()
    monkeypatch.setattr(hk, "make_hotkey_backend", lambda: fake)
    trig = hk.HotkeyTrigger({"dictate": ["ctrl+win"]}, push_to_talk={"dictate"})
    async with trig:
        assert trig.chord_is_down("dictate") is None


def test_a_raising_backend_callback_never_kills_the_poller_thread():
    """The Windows poller calls handlers bare; an exception escaping ours
    would end the thread and every global shortcut with it (BUG-191)."""
    import jarvis.trigger.hotkey as hk

    trig = hk.HotkeyTrigger({"dictate": ["ctrl+win"]}, push_to_talk={"dictate"})

    class _ClosingLoop:
        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, *_args) -> None:
            raise RuntimeError("Event loop is closed")

    trig._loop = _ClosingLoop()  # type: ignore[assignment]
    handler = trig._make_handler("dictate_release")
    handler()  # must not raise
