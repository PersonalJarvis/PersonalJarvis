"""Tests for the cross-platform hotkey backends (Wave 1.4; AD-6/AD-7/AD-8).

These lock the new seam introduced when the Windows ``global-hotkeys`` logic was
relocated behind a ``HotkeyBackend`` ``Protocol`` and ``pynput`` / no-op siblings
were added:

* ``make_hotkey_backend()`` selects the right class per platform / capability,
  and never raises or returns ``GlobalHotkeysBackend`` off Windows (AD-8).
* The relocated Windows refcount still flips the single shared checker on the
  0<->1 boundary (the BUG fix that kept two triggers from double-firing).
* ``NoopBackend`` logs its English Wayland message exactly once, then no-ops
  every call without raising (AD-OE6).
* ``PynputBackend`` translates the combo vocabulary correctly and imports
  ``pynput`` lazily (so this module imports clean on a box without it).

The strategy follows the brief: logic tests run on Windows (factory selection,
refcount boundary, noop); anything needing the *real* ``pynput`` library is
``importorskip`` + ``skipif(win32)`` so it skips cleanly here.
"""

from __future__ import annotations

import inspect
import logging
import sys

import pytest

from tests.fakes.fake_global_hotkeys import FakeGlobalHotkeys

# ----------------------------------------------------------------------
# Factory selection (AD-8) — pure logic, runs on every OS leg.
# ----------------------------------------------------------------------


@pytest.fixture()
def patch_platform(monkeypatch):
    """Return a helper that pins ``detect_platform`` + ``detect_capabilities``.

    Patches both the source modules and the names re-imported inside the factory
    so ``make_hotkey_backend`` sees the fake platform/capability shape.
    """
    import jarvis.platform as plat
    import jarvis.platform.capabilities as caps_mod

    def _apply(platform_name: str, has_hotkey: bool) -> None:
        monkeypatch.setattr(plat, "detect_platform", lambda: platform_name)
        fake_caps = caps_mod.Capabilities(
            platform=platform_name if platform_name in ("win32", "darwin", "linux") else "linux",
            has_hotkey=has_hotkey,
            has_ax_tree=False,
            has_overlay=False,
            has_pty=False,
            has_elevation=False,
            display_present=True,
            is_wayland=not has_hotkey,
            ax_permission_granted=None,
            has_cursor=False,
        )
        monkeypatch.setattr(caps_mod, "detect_capabilities", lambda: fake_caps)

    return _apply


def test_factory_selects_global_hotkeys_on_windows(patch_platform):
    from jarvis.trigger.backends import make_hotkey_backend
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    patch_platform("win32", has_hotkey=True)
    backend = make_hotkey_backend()
    assert isinstance(backend, GlobalHotkeysBackend)


def test_factory_selects_quartz_on_macos_with_hotkey(patch_platform):
    """macOS gets the TSM-free Quartz tap backend, never pynput (BUG-077)."""
    from jarvis.trigger.backends import make_hotkey_backend
    from jarvis.trigger.backends.pynput import PynputBackend
    from jarvis.trigger.backends.quartz import QuartzHotkeyBackend

    patch_platform("darwin", has_hotkey=True)
    backend = make_hotkey_backend()
    assert isinstance(backend, QuartzHotkeyBackend)
    assert not isinstance(backend, PynputBackend)


def test_factory_selects_pynput_on_linux_x11(patch_platform):
    from jarvis.trigger.backends import make_hotkey_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    patch_platform("linux", has_hotkey=True)
    backend = make_hotkey_backend()
    assert isinstance(backend, PynputBackend)


def test_factory_selects_noop_on_wayland(patch_platform):
    """Wayland → has_hotkey False → NoopBackend, never raises (AD-8)."""
    from jarvis.trigger.backends import make_hotkey_backend
    from jarvis.trigger.backends.noop import NoopBackend

    patch_platform("linux", has_hotkey=False)
    backend = make_hotkey_backend()
    assert isinstance(backend, NoopBackend)


def test_factory_never_returns_global_hotkeys_off_windows(patch_platform):
    from jarvis.trigger.backends import make_hotkey_backend
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    for platform_name, has_hotkey in (("darwin", True), ("linux", True), ("linux", False)):
        patch_platform(platform_name, has_hotkey=has_hotkey)
        backend = make_hotkey_backend()
        assert not isinstance(backend, GlobalHotkeysBackend)


# ----------------------------------------------------------------------
# GlobalHotkeysBackend — relocated Windows logic + refcount boundary (AD-7).
# ----------------------------------------------------------------------


@pytest.fixture()
def fake_gh():
    """Install a fresh FakeGlobalHotkeys + reset the relocated refcount."""
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


def _rows():
    """One toggle binding row in the normalized global_hotkeys form."""
    fired: list[str] = []
    return [["f1 + f2", None, lambda: fired.append("hangup")]], fired


def test_global_backend_refcount_zero_to_one_starts_checker(fake_gh):
    import jarvis.trigger.backends.global_hotkeys as ghb
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    rows, _ = _rows()
    backend = GlobalHotkeysBackend()
    backend.register(rows)
    assert ghb._CHECKER_REFCOUNT == 0
    assert not fake_gh.checker_running

    backend.start()
    assert ghb._CHECKER_REFCOUNT == 1  # 0->1 boundary started the checker
    assert fake_gh.checker_running
    assert fake_gh.start_calls == 1

    backend.stop()
    assert ghb._CHECKER_REFCOUNT == 0  # 1->0 boundary stopped it
    assert not fake_gh.checker_running


def test_global_backend_two_instances_share_one_checker(fake_gh):
    """Relocated single-checker invariant: peak live checkers stays at 1."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    a = GlobalHotkeysBackend()
    a.register([["f3 + f4", None, lambda: None]])
    a.start()
    b = GlobalHotkeysBackend()
    b.register([["control + alt + shift + k", None, lambda: None]])
    b.start()
    assert fake_gh.checker_running
    a.stop()
    assert fake_gh.checker_running  # b still live
    b.stop()
    assert not fake_gh.checker_running
    assert fake_gh.peak_live == 1


def test_global_backend_unregister_removes_by_string(fake_gh):
    """REGRESSION: unregister must pass combo STRINGS, never the rows."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    backend = GlobalHotkeysBackend()
    backend.register([["f1 + f2", None, lambda: None]])
    backend.start()
    backend.stop()
    backend.unregister()
    assert fake_gh.registered == {}
    for call in fake_gh.remove_calls:
        for item in call:
            assert isinstance(item, str), f"remove_hotkeys got non-string: {item!r}"


def test_global_backend_register_failure_degrades(fake_gh):
    """A register failure leaves the backend inert and the refcount balanced."""
    import jarvis.trigger.backends.global_hotkeys as ghb
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    fake_gh.register_error = Exception("simulated register failure")
    backend = GlobalHotkeysBackend()
    backend.register([["f1 + f2", None, lambda: None]])
    assert backend._gh is None  # degraded
    backend.start()  # no-op when degraded
    assert ghb._CHECKER_REFCOUNT == 0
    assert fake_gh.start_calls == 0


def test_global_backend_one_bad_combo_does_not_disable_others(fake_gh):
    """A single unregisterable combo must NOT take the other hotkeys down.

    The old code registered all bindings in one ``register_hotkeys(all)`` call,
    so one unknown key name raised and EVERY hotkey (incl. F1+F2) died. Now each
    binding is armed individually: the bad one is skipped, the rest stay live.
    """
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    fired: list[str] = []
    # "numpad_x" stands in for any combo the library cannot register.
    fake_gh.register_error_combos = {"numpad_x"}
    backend = GlobalHotkeysBackend()
    backend.register(
        [
            ["f1 + f2", None, lambda: fired.append("hangup")],
            ["numpad_x", None, lambda: fired.append("bad")],
            ["f3 + f4", None, lambda: fired.append("call")],
        ]
    )
    # The two good combos registered; the bad one was skipped — not a degrade.
    assert backend._gh is not None
    assert "f1+f2" in fake_gh.registered
    assert "f3+f4" in fake_gh.registered
    assert "numpad_x" not in fake_gh.registered
    # Teardown must only try to remove the combos that actually registered.
    assert backend._combo_strings == ["f1 + f2", "f3 + f4"]

    backend.start()
    fake_gh.fire("f1 + f2")
    fake_gh.fire("f3 + f4")
    assert fired == ["hangup", "call"]  # both good hotkeys still fire
    backend.stop()


def test_global_backend_all_combos_bad_degrades(fake_gh):
    """If NOT ONE combo registers, degrade so the checker never starts empty."""
    import jarvis.trigger.backends.global_hotkeys as ghb
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    fake_gh.register_error_combos = {"numpad_x"}
    backend = GlobalHotkeysBackend()
    backend.register([["numpad_x", None, lambda: None]])
    assert backend._gh is None  # degraded — nothing registered
    backend.start()  # no-op
    assert ghb._CHECKER_REFCOUNT == 0
    assert fake_gh.start_calls == 0


def test_global_backend_missing_package_degrades():
    """No global_hotkeys package → register degrades to a no-op, no raise."""
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    saved = sys.modules.get("global_hotkeys")
    sys.modules["global_hotkeys"] = None  # forces ImportError on `import`
    try:
        backend = GlobalHotkeysBackend()
        backend.register([["f1 + f2", None, lambda: None]])  # must not raise
        assert backend._gh is None
        backend.start()  # no-op
        backend.stop()  # no-op
        backend.unregister()  # no-op
    finally:
        if saved is not None:
            sys.modules["global_hotkeys"] = saved
        else:
            sys.modules.pop("global_hotkeys", None)


# ----------------------------------------------------------------------
# NoopBackend — logs once, then no-ops, never raises (AD-8 / AD-OE6).
# ----------------------------------------------------------------------


def test_noop_backend_logs_once_then_no_ops(caplog):
    import jarvis.trigger.backends.noop as noop_mod
    from jarvis.trigger.backends.noop import NoopBackend

    noop_mod._reset_noop_log_flag_for_tests()
    with caplog.at_level(logging.INFO, logger="jarvis.trigger.backends.noop"):
        NoopBackend()
        NoopBackend()  # second construction must NOT log again
    wayland_logs = [r for r in caplog.records if "Wayland" in r.getMessage()]
    assert len(wayland_logs) == 1, "the Wayland message must log exactly once"


def test_noop_backend_methods_never_raise():
    from jarvis.trigger.backends.noop import NoopBackend

    backend = NoopBackend()
    # Every lifecycle call is a safe no-op.
    backend.register([["f1 + f2", None, lambda: None]])
    backend.start()
    backend.stop()
    backend.unregister()
    assert backend.received_any_event() is False


def test_noop_backend_message_is_english():
    """The degrade message is English (output-language policy) + mentions wake."""
    import jarvis.trigger.backends.noop as noop_mod

    # Inspect the source so the assertion does not depend on log capture timing.
    src = inspect.getsource(noop_mod.NoopBackend._explain_once)
    assert "wake word" in src
    assert "Wayland" in src


# ----------------------------------------------------------------------
# PynputBackend — combo translation logic (pure, no pynput needed).
# ----------------------------------------------------------------------


def test_pynput_combo_translation_modifiers_and_key():
    from jarvis.trigger.backends.pynput import _parse_combo_tokens

    assert _parse_combo_tokens("control + alt + j") == ("ctrl", "alt", "j")


def test_pynput_combo_translation_fkeys_passthrough():
    from jarvis.trigger.backends.pynput import _parse_combo_tokens

    assert _parse_combo_tokens("f1 + f2") == ("f1", "f2")
    assert _parse_combo_tokens("f3 + f4") == ("f3", "f4")


def test_pynput_backend_register_does_not_import_pynput():
    """register() only stashes rows — no pynput import, so it works here."""
    from jarvis.trigger.backends.pynput import PynputBackend

    backend = PynputBackend()
    backend.register([["control + alt + j", lambda: None, lambda: None]])
    assert backend.received_any_event() is False
    backend.unregister()  # no raise


def test_pynput_backend_start_degrades_without_pynput(caplog):
    """When pynput is absent, start() logs + degrades — never raises (AD-6)."""
    from jarvis.trigger.backends.pynput import PynputBackend

    if sys.modules.get("pynput") is not None and _pynput_importable():
        pytest.skip("pynput is installed here — degrade path not exercised")
    backend = PynputBackend()
    backend.register([["control + alt + j", None, lambda: None]])
    with caplog.at_level(logging.WARNING, logger="jarvis.trigger.backends.pynput"):
        backend.start()  # must not raise
    backend.stop()  # idempotent, never raises
    assert backend._listener is None


def _pynput_importable() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("pynput") is not None
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------
# Real-pynput integration — skips cleanly on Windows / where pynput is absent.
# ----------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="pynput global hooks are not the Windows path (AD-8)",
)
def test_pynput_backend_real_listener_lifecycle():
    pytest.importorskip("pynput")
    from jarvis.trigger.backends.pynput import PynputBackend, _reset_listener_state_for_tests

    _reset_listener_state_for_tests()
    backend = PynputBackend()
    backend.register([["control + alt + j", lambda: None, lambda: None]])
    # On a headless CI runner with no X server the Listener may fail to start;
    # the backend degrades (listener None) rather than raising — assert no crash.
    backend.start()
    backend.stop()
    assert backend._listener is None


# ----------------------------------------------------------------------
# macOS Accessibility preflight (BUG-058) — pure logic, runs on every leg.
# ----------------------------------------------------------------------


def _install_fake_pynput(monkeypatch, built: list) -> None:
    import types

    class _FakeListener:
        def __init__(self, **kwargs) -> None:
            built.append(kwargs)

        def start(self) -> None: ...

        def stop(self) -> None: ...

    fake_pynput = types.ModuleType("pynput")
    fake_pynput.keyboard = types.SimpleNamespace(Listener=_FakeListener)
    monkeypatch.setitem(sys.modules, "pynput", fake_pynput)


def test_pynput_backend_darwin_without_ax_grant_degrades(monkeypatch, caplog):
    # pynput's darwin backend creates a Quartz event tap on its own internal
    # thread; without the Accessibility grant that native init can abort the
    # whole process (uncatchable — BUG-058 class). The backend must preflight
    # AXIsProcessTrusted and degrade instead of touching pynput at all.
    import jarvis.trigger.backends.pynput as pynput_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    built: list = []
    _install_fake_pynput(monkeypatch, built)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(pynput_backend, "_macos_hotkey_permissions_granted", lambda: False)
    backend = PynputBackend()
    with caplog.at_level(logging.WARNING):
        backend.start()
    assert built == []  # no Listener constructed under the missing grant
    assert "accessibility" in caplog.text.lower()


def test_pynput_backend_darwin_unverifiable_grant_degrades(monkeypatch, caplog):
    # pyobjc absent -> probe returns None -> fail closed on darwin.
    import jarvis.trigger.backends.pynput as pynput_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    built: list = []
    _install_fake_pynput(monkeypatch, built)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(
        pynput_backend,
        "_macos_hotkey_permissions_granted",
        lambda: False,
    )
    backend = PynputBackend()
    with caplog.at_level(logging.WARNING):
        backend.start()
    assert built == []


def test_pynput_backend_darwin_with_grant_starts_listener(monkeypatch):
    import jarvis.trigger.backends.pynput as pynput_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    built: list = []
    _install_fake_pynput(monkeypatch, built)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(pynput_backend, "_macos_hotkey_permissions_granted", lambda: True)
    monkeypatch.setattr(pynput_backend, "_macos_layout_guard_ready", lambda: True)
    backend = PynputBackend()
    backend.start()
    assert len(built) == 1  # grant present -> hotkeys arm normally


def test_pynput_backend_darwin_without_layout_snapshot_degrades(monkeypatch, caplog):
    # BUG-077: macOS 15 kills the process (uncatchable SIGILL) when pynput's
    # listener thread calls the TIS keyboard-layout APIs off the main thread.
    # With no main-thread layout snapshot the backend must degrade — no
    # Listener at all — instead of letting the OS abort the app.
    import jarvis.trigger.backends.pynput as pynput_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    built: list = []
    _install_fake_pynput(monkeypatch, built)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(pynput_backend, "_macos_hotkey_permissions_granted", lambda: True)
    monkeypatch.setattr(pynput_backend, "_macos_layout_guard_ready", lambda: False)
    backend = PynputBackend()
    with caplog.at_level(logging.WARNING):
        backend.start()
    assert built == []
    assert "keyboard-layout" in caplog.text


def test_pynput_backend_darwin_layout_guard_crash_degrades(monkeypatch, caplog):
    # A raising guard must never propagate out of start() (AD-6).
    import jarvis.trigger.backends.pynput as pynput_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    built: list = []
    _install_fake_pynput(monkeypatch, built)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(pynput_backend, "_macos_hotkey_permissions_granted", lambda: True)

    def _boom() -> bool:
        raise RuntimeError("guard exploded")

    monkeypatch.setattr(pynput_backend, "_macos_layout_guard_ready", _boom)
    backend = PynputBackend()
    with caplog.at_level(logging.WARNING):
        backend.start()
    assert built == []


def test_pynput_backend_off_darwin_needs_no_probe(monkeypatch):
    # AD-7: the preflight is darwin-only; Linux/Windows never consult it.
    import jarvis.trigger.backends.pynput as pynput_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    built: list = []
    _install_fake_pynput(monkeypatch, built)
    monkeypatch.setattr("sys.platform", "linux")

    def _boom() -> None:
        raise AssertionError("probe consulted off darwin")

    monkeypatch.setattr(pynput_backend, "_macos_hotkey_permissions_granted", _boom)
    backend = PynputBackend()
    backend.start()
    assert len(built) == 1


def test_pynput_backend_revoked_permission_suppresses_live_callback(monkeypatch):
    import jarvis.trigger.backends.pynput as pynput_backend
    from jarvis.trigger.backends.pynput import PynputBackend

    built: list = []
    allowed = {"value": True}
    _install_fake_pynput(monkeypatch, built)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(
        pynput_backend,
        "_macos_hotkey_permissions_granted",
        lambda: allowed["value"],
    )
    monkeypatch.setattr(pynput_backend, "_macos_layout_guard_ready", lambda: True)
    fired: list[str] = []
    backend = PynputBackend()
    backend.register([["control + j", lambda: fired.append("call"), None]])
    backend.start()

    allowed["value"] = False
    backend._held.update({"ctrl", "j"})
    backend._reconcile()

    assert fired == []
    assert backend._held == set()
