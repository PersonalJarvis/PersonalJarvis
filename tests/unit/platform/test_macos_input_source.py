"""Tests for the macOS TIS main-thread layout guard (BUG-065).

macOS 15 kills the process with an uncatchable SIGILL when the TIS
keyboard-layout APIs run off the main thread — which is where pynput calls
them (listener thread / backend-thread ``Controller()``). The guard caches a
main-thread layout snapshot and patches pynput to reuse it off-main.

All platform branches are exercised from any host: ``sys.platform`` is
monkeypatched and the raw ctypes capture is replaced by a fake, so no test
touches a real macOS framework.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import types

import pytest

from jarvis.platform import macos_input_source as mis

FAKE_CONTEXT = (44, b"layout-bytes")


@pytest.fixture(autouse=True)
def _clean_state():
    mis._reset_for_tests()
    yield
    mis._reset_for_tests()


def _force_darwin(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")


def _install_fake_pynput_util(monkeypatch, tis_calls: list):
    """A fake ``pynput._util.darwin`` whose ``keycode_context`` records that
    the real (TIS-touching) implementation ran."""

    @contextlib.contextmanager
    def _original_keycode_context():
        tis_calls.append("tis")
        yield FAKE_CONTEXT

    util_darwin = types.ModuleType("pynput._util.darwin")
    util_darwin.keycode_context = _original_keycode_context
    util_pkg = types.ModuleType("pynput._util")
    util_pkg.darwin = util_darwin
    kbd_darwin = types.ModuleType("pynput.keyboard._darwin")
    kbd_darwin.keycode_context = _original_keycode_context
    kbd_pkg = types.ModuleType("pynput.keyboard")
    kbd_pkg._darwin = kbd_darwin
    pynput_pkg = types.ModuleType("pynput")
    pynput_pkg._util = util_pkg
    pynput_pkg.keyboard = kbd_pkg
    monkeypatch.setitem(sys.modules, "pynput", pynput_pkg)
    monkeypatch.setitem(sys.modules, "pynput._util", util_pkg)
    monkeypatch.setitem(sys.modules, "pynput._util.darwin", util_darwin)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kbd_pkg)
    monkeypatch.setitem(sys.modules, "pynput.keyboard._darwin", kbd_darwin)
    return util_darwin, kbd_darwin


# --- prime_keyboard_layout_cache -------------------------------------------


def test_prime_is_noop_true_off_darwin(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert mis.prime_keyboard_layout_cache() is True
    assert mis.keyboard_layout_cache_ready() is False


def test_prime_refuses_off_main_thread(monkeypatch):
    _force_darwin(monkeypatch)
    monkeypatch.setattr(mis, "_on_main_thread", lambda: False)
    monkeypatch.setattr(
        mis,
        "_capture_layout_context",
        lambda: pytest.fail("TIS capture must never run off the main thread"),
    )
    assert mis.prime_keyboard_layout_cache() is False
    assert mis.keyboard_layout_cache_ready() is False


def test_prime_caches_on_main_thread(monkeypatch):
    _force_darwin(monkeypatch)
    monkeypatch.setattr(mis, "_capture_layout_context", lambda: FAKE_CONTEXT)
    assert mis.prime_keyboard_layout_cache() is True
    assert mis.keyboard_layout_cache_ready() is True
    # Second call is a cache hit — no re-capture.
    monkeypatch.setattr(
        mis, "_capture_layout_context", lambda: pytest.fail("re-captured")
    )
    assert mis.prime_keyboard_layout_cache() is True


def test_prime_survives_capture_failure(monkeypatch, caplog):
    _force_darwin(monkeypatch)

    def _boom():
        raise OSError("no Carbon here")

    monkeypatch.setattr(mis, "_capture_layout_context", _boom)
    assert mis.prime_keyboard_layout_cache() is False
    assert mis.keyboard_layout_cache_ready() is False


def test_prime_rejects_snapshot_without_layout_bytes(monkeypatch):
    # A (keyboard_type, None) context would NULL-deref in UCKeyTranslate
    # later — it must not be cached.
    _force_darwin(monkeypatch)
    monkeypatch.setattr(mis, "_capture_layout_context", lambda: (44, None))
    assert mis.prime_keyboard_layout_cache() is False
    assert mis.keyboard_layout_cache_ready() is False


# --- install_pynput_layout_guard -------------------------------------------


def test_guard_noop_true_off_darwin(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert mis.install_pynput_layout_guard() is True


def test_guard_false_when_pynput_missing(monkeypatch):
    _force_darwin(monkeypatch)
    for name in list(sys.modules):
        if name == "pynput" or name.startswith("pynput."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "pynput", None)  # force ImportError
    assert mis.install_pynput_layout_guard() is False


def test_guarded_context_off_main_uses_cache_never_tis(monkeypatch):
    _force_darwin(monkeypatch)
    tis_calls: list = []
    util_darwin, kbd_darwin = _install_fake_pynput_util(monkeypatch, tis_calls)
    monkeypatch.setattr(mis, "_capture_layout_context", lambda: FAKE_CONTEXT)
    assert mis.ensure_pynput_layout_guard() is True
    tis_calls.clear()

    # Both import sites must be rebound to the guard.
    assert util_darwin.keycode_context is kbd_darwin.keycode_context

    result: dict = {}

    def _off_main():
        with util_darwin.keycode_context() as context:
            result["context"] = context

    worker = threading.Thread(target=_off_main)
    worker.start()
    worker.join()
    assert result["context"] == FAKE_CONTEXT
    assert tis_calls == []  # the real TIS path never ran off-main


def test_guarded_context_off_main_without_cache_raises(monkeypatch):
    _force_darwin(monkeypatch)
    tis_calls: list = []
    util_darwin, _ = _install_fake_pynput_util(monkeypatch, tis_calls)
    assert mis.install_pynput_layout_guard() is True

    raised: dict = {}

    def _off_main():
        try:
            with util_darwin.keycode_context():
                pass
        except RuntimeError as exc:
            raised["exc"] = exc

    worker = threading.Thread(target=_off_main)
    worker.start()
    worker.join()
    assert "main thread" in str(raised["exc"])
    assert tis_calls == []


def test_guarded_context_on_main_runs_original_and_refreshes_cache(monkeypatch):
    _force_darwin(monkeypatch)
    tis_calls: list = []
    util_darwin, _ = _install_fake_pynput_util(monkeypatch, tis_calls)
    assert mis.install_pynput_layout_guard() is True

    with util_darwin.keycode_context() as context:
        assert context == FAKE_CONTEXT
    assert tis_calls == ["tis"]
    # The main-thread pass-through primed the cache as a side effect.
    assert mis.keyboard_layout_cache_ready() is True


def test_ensure_reports_false_without_cache(monkeypatch):
    # Off the main thread with no snapshot: the guard installs (so a later
    # Controller() raises instead of SIGILLing) but ensure() reports unsafe.
    _force_darwin(monkeypatch)
    tis_calls: list = []
    _install_fake_pynput_util(monkeypatch, tis_calls)
    monkeypatch.setattr(mis, "_on_main_thread", lambda: False)
    assert mis.ensure_pynput_layout_guard() is False


def test_ensure_true_off_darwin(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert mis.ensure_pynput_layout_guard() is True
