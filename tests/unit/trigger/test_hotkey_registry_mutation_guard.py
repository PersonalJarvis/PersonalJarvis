"""The Windows hotkey registry may only be written while the poller is paused.

Live forensic 2026-08-10 18:39:29 (Computer-Use mission "open Elon's post"):

    Thread 'Thread-34 (run)' died on an unhandled exception
      File ".../global_hotkeys/hotkey_checker.py", line 216, in run
        for id in id_list:
    RuntimeError: dictionary changed size during iteration

``HotkeyChecker.run`` binds ``id_list = self.hotkeys.keys()`` ONCE and then
iterates that LIVE dict view every 20 ms for the life of the thread. Arming the
Computer-Use cancel key (``cu_cancel=[escape]``) from the mission thread resized
that dict mid-iteration, so the poller died — and with it the Escape kill-switch
plus every other global shortcut for the rest of the process. Nothing surfaced
to the user; the keys just stopped working.

The library exposes no lock, so ``_registry_mutation`` pauses the shared poller,
lets it leave its loop, performs the write, and restarts it.
"""
from __future__ import annotations

import sys

import pytest

from tests.fakes.fake_global_hotkeys import FakeGlobalHotkeys


@pytest.fixture()
def strict_gh():
    """A fake whose poller dies on a registry write, exactly like the real one."""
    import jarvis.trigger.backends.global_hotkeys as ghb

    fake = FakeGlobalHotkeys()
    fake.strict_poller = True
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


def test_arming_a_second_trigger_does_not_kill_the_running_poller(strict_gh):
    """The exact live sequence: the voice trigger polls, a Computer-Use mission
    arms its cancel key, the mission ends and releases it. The poller must
    survive all of it and still deliver presses."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    fired: list[str] = []
    voice = GlobalHotkeysBackend()
    voice.register([["f1 + f2", None, lambda: fired.append("hangup")]])
    voice.start()
    assert strict_gh.checker_running

    cu_cancel = GlobalHotkeysBackend()
    cu_cancel.register([["escape", None, lambda: fired.append("cancel")]])
    cu_cancel.start()
    cu_cancel.stop()
    cu_cancel.unregister()

    assert not strict_gh.poller_crashed
    assert strict_gh.checker_running

    strict_gh.fire("f1 + f2")
    assert fired == ["hangup"]

    voice.stop()
    voice.unregister()


def test_first_registration_needs_no_pause(strict_gh):
    """With no poller running yet there is nothing to protect — and nothing to
    restart, so the checker must not be started behind ``start()``'s back."""
    import jarvis.trigger.backends.global_hotkeys as ghb
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    backend = GlobalHotkeysBackend()
    backend.register([["f3 + f4", None, lambda: None]])

    assert strict_gh.start_calls == 0
    assert strict_gh.stop_calls == 0
    assert ghb._CHECKER_REFCOUNT == 0


def test_paused_poller_is_restarted_after_the_write(strict_gh):
    """Pause and resume are balanced: one stop, one start, never a leak that
    leaves the shortcuts dead or a second poller double-firing."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    voice = GlobalHotkeysBackend()
    voice.register([["f1 + f2", None, lambda: None]])
    voice.start()
    starts_before, stops_before = strict_gh.start_calls, strict_gh.stop_calls

    other = GlobalHotkeysBackend()
    other.register([["escape", None, lambda: None]])

    assert strict_gh.start_calls == starts_before + 1
    assert strict_gh.stop_calls == stops_before + 1
    assert strict_gh.peak_live == 1  # never two pollers at once
    assert strict_gh.checker_running

    voice.stop()


def test_teardown_write_also_runs_under_the_guard(strict_gh):
    """``unregister`` removes combos by string — that is a registry write too,
    and it happened at exactly the moment the live poller died."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    voice = GlobalHotkeysBackend()
    voice.register([["f1 + f2", None, lambda: None]])
    voice.start()

    doomed = GlobalHotkeysBackend()
    doomed.register([["escape", None, lambda: None]])
    doomed.unregister()

    assert not strict_gh.poller_crashed
    assert strict_gh.checker_running

    voice.stop()
