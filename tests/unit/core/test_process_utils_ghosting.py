"""Guards for ``disable_windows_app_ghosting`` (BUG-118).

The bar's magenta colour key belongs to the window Jarvis owns. When that
window stops pumping messages, Windows substitutes an UNLAYERED ghost window at
the same rectangle, the key is never applied to it, and the padding around the
pill reaches the screen as an opaque black box. The helper turns the
substitution off; these tests pin its contract, which has to hold on the two
OSes that have no such behaviour just as much as on Windows.
"""
from __future__ import annotations

import sys

import pytest

from jarvis.core import process_utils


@pytest.fixture(autouse=True)
def _reset_flag():
    """Each test starts from 'not yet disabled' — the flag is process-global."""
    original = process_utils._ghosting_disabled
    process_utils._ghosting_disabled = False
    yield
    process_utils._ghosting_disabled = original


def test_is_a_quiet_no_op_off_windows(monkeypatch):
    """macOS/Linux have no ghost window — report False, never raise, never
    touch ctypes."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert process_utils.disable_windows_app_ghosting() is False
    monkeypatch.setattr(sys, "platform", "linux")
    assert process_utils.disable_windows_app_ghosting() is False
    # A no-op must not claim the process is protected.
    assert process_utils._ghosting_disabled is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only API")
def test_disables_ghosting_on_windows():
    assert process_utils.disable_windows_app_ghosting() is True
    assert process_utils._ghosting_disabled is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only API")
def test_second_call_is_free():
    """Idempotent: both overlay surfaces call it, and the API cannot be undone,
    so a repeat must short-circuit rather than re-enter user32."""
    assert process_utils.disable_windows_app_ghosting() is True
    calls = []

    class _Boom:
        def __getattr__(self, name):  # any ctypes access is a failure here
            calls.append(name)
            raise AssertionError("second call must not reach ctypes")

    import ctypes

    original = ctypes.windll
    try:
        ctypes.windll = _Boom()
        assert process_utils.disable_windows_app_ghosting() is True
    finally:
        ctypes.windll = original
    assert calls == []


def test_a_failing_api_never_propagates(monkeypatch):
    """Cosmetic hardening: a Windows build without the export (or a locked-down
    host) must degrade to False, not take the overlay's boot down with it."""
    monkeypatch.setattr(sys, "platform", "win32")

    class _Windll:
        @property
        def user32(self):
            raise OSError("user32 unavailable")

    import ctypes

    monkeypatch.setattr(ctypes, "windll", _Windll(), raising=False)
    assert process_utils.disable_windows_app_ghosting() is False
    assert process_utils._ghosting_disabled is False


def test_exported_from_the_module():
    """Both overlay surfaces import it by name; keep it in __all__."""
    assert "disable_windows_app_ghosting" in process_utils.__all__
