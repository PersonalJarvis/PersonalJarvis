"""Regression tests for the F1+F2 (hangup) / F3+F4 (call) global hotkeys.

These lock the lifecycle of ``jarvis.trigger.hotkey.HotkeyTrigger`` against the
bug that silently bricked the shortcuts after an in-process pipeline restart:

* ``__aexit__`` handed ``global_hotkeys.remove_hotkeys`` the full
  ``[combo, on_press, on_release]`` rows instead of plain combo **strings**,
  so removal raised ``AttributeError`` (swallowed) and the module-level
  singleton kept the stale registration.
* The next ``__aenter__`` then hit "The hotkey [...] is already registered."
  inside the *un-wrapped* ``register_hotkeys`` call, which aborted the whole
  registration — so **every** hotkey (call AND hangup) went dead.

The tests run against ``FakeGlobalHotkeys`` (no Windows hooks, no OS thread),
which faithfully reproduces the real module's contract — duplicate-combo
rejection and the string-only ``remove_hotkeys`` signature included.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from tests.fakes.fake_global_hotkeys import FakeGlobalHotkeys

# Exactly the bindings the live SpeechPipeline wires (pipeline.py defaults).
CALL_COMBOS = ["ctrl+right_alt+j", "f3+f4"]
HANGUP_COMBOS = ["f1+f2"]
LIVE_BINDINGS = {"call": CALL_COMBOS, "hangup": HANGUP_COMBOS}


@pytest.fixture()
def fake_gh():
    """Install a fresh FakeGlobalHotkeys into ``sys.modules`` for the test.

    Also resets the module-level checker refcount so the single-checker
    invariant is asserted from a clean slate regardless of test ordering.
    """
    import jarvis.trigger.hotkey as hk

    fake = FakeGlobalHotkeys()
    saved = sys.modules.get("global_hotkeys")
    sys.modules["global_hotkeys"] = fake
    hk._reset_checker_state_for_tests()
    try:
        yield fake
    finally:
        hk._reset_checker_state_for_tests()
        if saved is not None:
            sys.modules["global_hotkeys"] = saved
        else:
            sys.modules.pop("global_hotkeys", None)


async def _next_event(trig, timeout_s: float = 1.0) -> str:
    return await asyncio.wait_for(trig.events().__anext__(), timeout_s)


# ----------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------

def test_normalize_combo_maps_modifiers_and_keeps_fkeys():
    from jarvis.trigger.hotkey import _normalize_combo

    assert _normalize_combo("ctrl+right_alt+j") == "control + alt + j"
    assert _normalize_combo("f1+f2") == "f1 + f2"
    assert _normalize_combo("f3+f4") == "f3 + f4"


# ----------------------------------------------------------------------
# Hotkey validation (editable PTT hotkey)
# ----------------------------------------------------------------------

import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    "combo",
    [
        "ctrl+right_alt+j",   # the default
        "ctrl+right_alt+k",
        "ctrl+shift+space",
        "f3+f4",              # two-key chord, no modifier — still safe
        "ctrl+alt+m",
    ],
)
def test_validate_hotkey_accepts_safe_combos(combo):
    from jarvis.trigger.hotkey import validate_hotkey

    ok, reason = validate_hotkey(combo)
    assert ok, f"{combo!r} should be valid, got: {reason}"


@_pytest.mark.parametrize(
    "combo",
    [
        "",                 # empty
        "   ",              # blank
        "ctrl+alt+shift",   # modifiers only — no real key
        "j",                # single bare key — fires while typing
        "win+j",            # Windows key reserved
        "alt+f4",           # closes windows
        "ctrl+c",           # copy / interrupt
    ],
)
def test_validate_hotkey_rejects_unsafe_combos(combo):
    from jarvis.trigger.hotkey import validate_hotkey

    ok, reason = validate_hotkey(combo)
    assert not ok, f"{combo!r} should be rejected"
    assert reason, "a rejection must carry a user-facing reason"


def test_validate_hotkey_allows_ctrl_c_when_part_of_larger_combo():
    """Ctrl+C alone is the interrupt; Ctrl+Shift+C is a different, safe combo."""
    from jarvis.trigger.hotkey import validate_hotkey

    ok, _ = validate_hotkey("ctrl+shift+c")
    assert ok


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------

async def test_enter_registers_all_call_and_hangup_combos(fake_gh):
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(LIVE_BINDINGS):
        assert "control+alt+j" in fake_gh.registered
        assert "f3+f4" in fake_gh.registered
        assert "f1+f2" in fake_gh.registered
        assert fake_gh.checker_running


async def test_hangup_combo_press_yields_hangup_event(fake_gh):
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(LIVE_BINDINGS) as trig:
        fake_gh.fire("f1+f2")
        assert await _next_event(trig) == "hangup"


async def test_both_call_combos_yield_call_event(fake_gh):
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(LIVE_BINDINGS) as trig:
        fake_gh.fire("f3+f4")
        assert await _next_event(trig) == "call"
        # Fire the *normalized* combo — that is the key the trigger registers
        # ("ctrl+right_alt+j" -> "control + alt + j"); the real package fires on
        # virtual-key codes, the fake matches on the registered string.
        fake_gh.fire("control + alt + j")
        assert await _next_event(trig) == "call"


# ----------------------------------------------------------------------
# Lifecycle — the actual bug
# ----------------------------------------------------------------------

async def test_exit_removes_hotkeys_with_string_format(fake_gh):
    """REGRESSION: __aexit__ must pass combo STRINGS to remove_hotkeys.

    The historical bug passed ``[combo, None, handler]`` rows, which the real
    ``remove_hotkeys`` rejects with AttributeError (the fake reproduces this).
    A clean exit must leave the singleton registry empty so the next run can
    re-register.
    """
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(LIVE_BINDINGS):
        pass
    # No AttributeError was swallowed, and everything was unregistered.
    assert fake_gh.registered == {}
    # Every payload handed to remove_hotkeys was a list of plain strings.
    for call in fake_gh.remove_calls:
        for item in call:
            assert isinstance(item, str), f"remove_hotkeys got non-string: {item!r}"


async def test_reentry_after_exit_does_not_raise_already_registered(fake_gh):
    """The proven in-process-restart scenario: enter -> exit -> enter again.

    Before the fix the second enter raised "already registered" and killed
    every hotkey. After the fix it re-registers cleanly.
    """
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(LIVE_BINDINGS):
        pass
    # Second lifecycle in the SAME process / SAME singleton must succeed.
    async with HotkeyTrigger(LIVE_BINDINGS) as trig2:
        assert "f1+f2" in fake_gh.registered
        fake_gh.fire("f1+f2")
        assert await _next_event(trig2) == "hangup"


async def test_exit_leaves_no_live_checker(fake_gh):
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(LIVE_BINDINGS):
        assert fake_gh.checker_running
    assert not fake_gh.checker_running


async def test_missing_global_hotkeys_degrades_gracefully():
    """Cloud-first VPS path: when the optional ``global_hotkeys`` package is
    not installed, entering the trigger must NOT crash the voice pipeline —
    it degrades to "no hotkeys" and voice still works via wake word.
    """
    from jarvis.trigger.hotkey import HotkeyTrigger

    saved = sys.modules.get("global_hotkeys")
    sys.modules["global_hotkeys"] = None  # forces ImportError on `import`
    try:
        async with HotkeyTrigger(LIVE_BINDINGS) as trig:
            assert trig is not None  # entered cleanly, no exception
            assert trig._gh is None  # degraded — no module handle
        # __aexit__ is also clean (no AttributeError on a None module).
    finally:
        if saved is not None:
            sys.modules["global_hotkeys"] = saved
        else:
            sys.modules.pop("global_hotkeys", None)


async def test_register_failure_degrades_without_crashing_the_pipeline(fake_gh):
    """If global_hotkeys.register_hotkeys raises (e.g. an invalid combo), the
    trigger must NOT propagate — that would crash the whole voice pipeline at
    `async with HotkeyTrigger(...)`. It degrades to "no hotkeys" (AD-OE6), and
    the shared checker refcount stays balanced so the next trigger is healthy.
    """
    import jarvis.trigger.hotkey as hk
    from jarvis.trigger.hotkey import HotkeyTrigger

    fake_gh.register_error = Exception("simulated register failure")
    async with HotkeyTrigger(LIVE_BINDINGS) as trig:
        assert trig._gh is None          # degraded, no crash
    assert hk._CHECKER_REFCOUNT == 0     # never incremented on failure
    assert fake_gh.start_calls == 0      # checker never started
    assert not fake_gh.checker_running


async def test_concurrent_instances_share_a_single_checker(fake_gh):
    """Two HotkeyTrigger instances alive at once (e.g. pipeline + kill-switch)
    must not spawn two checker loops — a duplicate loop double-fires every
    press. Peak live checkers stays at 1.
    """
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger({"call": ["f3+f4"]}):
        async with HotkeyTrigger({"kill": ["ctrl+alt+shift+k"]}):
            assert fake_gh.checker_running
    assert fake_gh.peak_live == 1
    assert not fake_gh.checker_running


# ----------------------------------------------------------------------
# Push-to-talk — both key edges (press starts recording, release submits)
# ----------------------------------------------------------------------

PTT_BINDINGS = {"ptt": ["ctrl+right_alt+j"], "hangup": HANGUP_COMBOS}


async def test_ptt_press_and_release_yield_distinct_edge_events(fake_gh):
    """A push-to-talk binding fires ``<name>_press`` on the down edge and
    ``<name>_release`` on the up edge — the two events the pipeline needs to
    start the recording on press and submit it on release."""
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(PTT_BINDINGS, push_to_talk={"ptt"}) as trig:
        fake_gh.fire_press("control + alt + j")
        assert await _next_event(trig) == "ptt_press"
        fake_gh.fire_release("control + alt + j")
        assert await _next_event(trig) == "ptt_release"


async def test_ptt_binding_registers_an_on_press_handler(fake_gh):
    """Unlike a normal toggle binding (on_release only), a push-to-talk combo
    must register a live on_press handler so the down edge is observable."""
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(PTT_BINDINGS, push_to_talk={"ptt"}):
        on_press, on_release = fake_gh.registered["control+alt+j"]
        assert on_press is not None, "push-to-talk needs the down edge"
        assert on_release is not None, "push-to-talk needs the up edge"


async def test_non_ptt_binding_stays_release_only(fake_gh):
    """A binding NOT marked push-to-talk keeps the legacy contract: only the
    on_release edge fires, so a held key triggers exactly once (not per
    key-repeat poll). The press edge must be ``None``."""
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(PTT_BINDINGS, push_to_talk={"ptt"}) as trig:
        on_press, on_release = fake_gh.registered["f1+f2"]
        assert on_press is None, "toggle binding must not fire on the down edge"
        assert on_release is not None
        # And firing the press edge yields nothing — only release does.
        fake_gh.fire_release("f1+f2")
        assert await _next_event(trig) == "hangup"


async def test_default_has_no_push_to_talk_bindings(fake_gh):
    """Without the push_to_talk argument every binding is a release-only
    toggle — the pre-PTT behaviour is preserved by default."""
    from jarvis.trigger.hotkey import HotkeyTrigger

    async with HotkeyTrigger(LIVE_BINDINGS):
        for combo in ("control+alt+j", "f3+f4", "f1+f2"):
            on_press, on_release = fake_gh.registered[combo]
            assert on_press is None
            assert on_release is not None
