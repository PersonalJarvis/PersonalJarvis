"""A ScreenCaptureKit wait that times out is PENDING, never DENIED, when the state explains it.

On macOS 15+ the first capture may show an OS confirmation (unverified), and the
shareable-content call then waits for the user. The seam must not read that as a
denial: when the live Screen Recording read says granted, or a request is in
flight, the reason is ``pending_confirmation``; otherwise it is a plain
``timeout`` (still not a denial). The framework is a hand-written stub whose
completion handler never fires; the permission state comes from the REAL service
on ``FakeTCC``. Nothing here ran on a real Mac.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from jarvis.platform import window_capture
from jarvis.platform.permission_service import get_permission_service
from jarvis.platform.permissions import PermissionId
from tests.fakes.fake_tcc import DialogPolicy, FakeTCC, TccService, install_port

_BBOX = {"left": 0, "top": 0, "width": 80, "height": 60}


@pytest.fixture
def silent_sck(monkeypatch):
    """A ScreenCaptureKit whose shareable-content handler never fires (a dialog is up)."""

    class _Content:
        @staticmethod
        def getShareableContentWithCompletionHandler_(_handler):  # noqa: N802 - Cocoa selector
            return None  # never calls back

    sck = types.SimpleNamespace(SCScreenshotManager=object(), SCShareableContent=_Content)
    appkit = types.SimpleNamespace(NSBitmapImageRep=object())
    monkeypatch.setitem(sys.modules, "ScreenCaptureKit", sck)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setattr(window_capture, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(window_capture, "_SCK_TIMEOUT_S", 0.01)


def _darwin(monkeypatch, **kwargs) -> FakeTCC:
    tcc = FakeTCC(**kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    return tcc


def test_timeout_with_a_live_grant_is_pending_confirmation(monkeypatch, silent_sck, caplog):
    _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])

    with caplog.at_level(logging.INFO, logger=window_capture.log.name):
        assert window_capture.grab_window(7, _BBOX) is None

    assert window_capture.last_grab_failure() == "pending_confirmation"
    assert "may be asking you to confirm" in caplog.text


def test_timeout_while_a_request_is_in_flight_is_pending_confirmation(monkeypatch, silent_sck):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    get_permission_service().ensure(PermissionId.SCREEN_RECORDING, feature="computer_use", wait_s=0)
    assert tcc.dialog_open(TccService.SCREEN_RECORDING)

    assert window_capture.grab_window(7, _BBOX) is None

    assert window_capture.last_grab_failure() == "pending_confirmation"


def test_timeout_nothing_explains_is_a_plain_timeout_not_a_denial(monkeypatch, silent_sck):
    tcc = _darwin(monkeypatch)  # never asked, not granted
    mark = tcc.mark()

    assert window_capture.grab_window(7, _BBOX) is None

    assert window_capture.last_grab_failure() == "timeout"
    assert tcc.calls_of("request", since=mark) == []


def test_the_classification_never_asks(monkeypatch, silent_sck):
    tcc = _darwin(monkeypatch)

    window_capture.grab_window(7, _BBOX)

    tcc.assert_no_prompts()


def test_a_later_grab_clears_the_previous_reason(monkeypatch):
    window_capture._failure.reason = "pending_confirmation"
    monkeypatch.setattr(window_capture, "detect_platform", lambda: "linux")

    window_capture.grab_window(7, _BBOX)

    assert window_capture.last_grab_failure() == ""
