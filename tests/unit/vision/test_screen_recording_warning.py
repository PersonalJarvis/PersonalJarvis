"""H1: a missing macOS Screen-Recording grant must surface a clear, honest message at
the screenshot capture point, instead of silently capturing the desktop wallpaper
(which would make Computer-Use click blind) - and the helpers that do so must never
ASK macOS: only a gesture entry point does (design-v2 section 3.5).

The REAL permission service runs on a REAL ``SystemPermissionPort`` that sits on
``FakeTCC`` (a stateful model of macOS privacy); its call log proves nothing was
requested. Nothing here ran on a real Mac: see the fidelity ledger in
``tests/fakes/fake_tcc.py``.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

from jarvis.platform import screen_access
from jarvis.platform.permission_service import get_permission_service
from jarvis.platform.permissions import PermissionId, PermissionState
from jarvis.platform.screen_access import ScreenCaptureRefused
from jarvis.vision import screenshot
from tests.fakes.fake_screen_pixels import textured_pixels, wallpaper_pixels
from tests.fakes.fake_tcc import FakeTCC, TccService, install_port, make_non_darwin_port

_MSG = "Screen Recording permission not granted"


def _darwin(monkeypatch, **kwargs) -> FakeTCC:
    tcc = FakeTCC(**kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    return tcc


def test_warns_once_when_screen_recording_is_not_granted(monkeypatch, caplog):
    tcc = _darwin(monkeypatch)

    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is True
        assert screenshot.warn_if_screen_recording_denied() is True

    assert caplog.text.count(_MSG) == 1
    assert "Settings > Permissions" not in caplog.text
    assert "Screen & System Audio Recording" in caplog.text
    # It only READS the state: nothing was asked, nothing prompted behind our back.
    tcc.assert_no_prompts()


def test_no_warning_when_granted(monkeypatch, caplog):
    _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])

    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is False

    assert _MSG not in caplog.text


def test_unavailable_native_probe_fails_closed(monkeypatch, caplog):
    _darwin(monkeypatch, missing_frameworks=["Quartz"])

    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is True

    assert _MSG in caplog.text


def test_permission_recovery_is_observed_without_restart(monkeypatch, caplog):
    tcc = _darwin(monkeypatch)

    with caplog.at_level(logging.INFO):
        assert screenshot.warn_if_screen_recording_denied() is True
        # The user flips the switch in System Settings while Jarvis runs: the
        # frozen preflight stays negative, the window-title oracle sees the grant.
        tcc.grant(TccService.SCREEN_RECORDING)
        assert screenshot.warn_if_screen_recording_denied() is False

    assert "available again" in caplog.text
    tcc.assert_no_prompts()


def test_non_darwin_reads_nothing_and_never_warns(monkeypatch, caplog):
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)

    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is False

    assert _MSG not in caplog.text
    tcc.assert_silent()


# ---------------------------------------------------------------------------
# Helpers refuse honestly and never ask
# ---------------------------------------------------------------------------


def test_capture_region_refuses_with_the_permission_named_and_asks_nothing(monkeypatch):
    pytest.importorskip("PIL")
    tcc = _darwin(monkeypatch)
    grabbed = []

    with pytest.raises(ScreenCaptureRefused) as refused:
        screenshot.capture_region(
            {"left": 0, "top": 0, "width": 4, "height": 4},
            grab=lambda bbox: grabbed.append(bbox),
        )

    assert grabbed == []
    assert str(refused.value).startswith("[permission_needed:screen_recording] ")
    # A RuntimeError subclass: every existing ``except RuntimeError`` keeps working.
    assert isinstance(refused.value, RuntimeError)
    tcc.assert_no_prompts()


def test_capture_region_grabs_once_the_grant_is_live(monkeypatch):
    pytest.importorskip("PIL")
    _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])

    data = screenshot.capture_region(
        {"left": 0, "top": 0, "width": 4, "height": 4},
        grab=lambda _bbox: textured_pixels((4, 4)),
    )

    assert data[:2] == b"\xff\xd8"


class _FakeSct:
    """mss.mss() stand-in whose grab returns a fixed frame."""

    frame: tuple[tuple[int, int], bytes] = ((8, 8), b"")

    monitors = [
        {"left": 0, "top": 0, "width": 8, "height": 8},
        {"left": 0, "top": 0, "width": 8, "height": 8},
    ]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def grab(self, _monitor):
        size, rgb = type(self).frame
        return types.SimpleNamespace(size=size, rgb=rgb)


def _fake_mss(monkeypatch, frame) -> None:
    sct = type("Sct", (_FakeSct,), {"frame": frame})
    fake = types.ModuleType("mss")
    fake.mss = sct  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mss", fake)


def test_vision_source_refuses_without_the_grant_and_never_asks(monkeypatch):
    pytest.importorskip("PIL")
    tcc = _darwin(monkeypatch)
    _fake_mss(monkeypatch, textured_pixels((8, 8)))
    source = screenshot.ScreenshotSource(save_blob=False, monitor_strategy="primary")

    with pytest.raises(ScreenCaptureRefused) as refused:
        source._capture_image()

    assert refused.value.reason == "needs_settings"
    tcc.assert_no_prompts()


def test_vision_source_wallpaper_frame_is_no_success_and_stays_background(monkeypatch):
    # The grant reads as granted (cached), then is revoked: the frame is the
    # wallpaper. The vision source is a background observer, so the episode it
    # opens has the background origin (inline status, no card) and no request.
    pytest.importorskip("PIL")
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING], preflight_frozen=())
    _fake_mss(monkeypatch, wallpaper_pixels((8, 8)))
    source = screenshot.ScreenshotSource(save_blob=False, monitor_strategy="primary")
    service = get_permission_service()
    assert service.check(PermissionId.SCREEN_RECORDING) is PermissionState.GRANTED
    tcc.deny(TccService.SCREEN_RECORDING)

    # The helper's own state read sees the revocation first (cache bypassed by the
    # deep fallback), so it refuses before grabbing; either way no wallpaper escapes.
    with pytest.raises(ScreenCaptureRefused):
        source._capture_image()

    assert tcc.dialogs_shown() == []
    assert tcc.requests() == []
    assert all(episode.origin != "user" for episode in service.outstanding())


def test_vision_source_non_darwin_is_unchanged(monkeypatch):
    pytest.importorskip("PIL")
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    _fake_mss(monkeypatch, wallpaper_pixels((8, 8)))
    source = screenshot.ScreenshotSource(save_blob=False, monitor_strategy="primary")

    captured = source._capture_image()

    assert captured is not None
    tcc.assert_silent()


def test_screen_access_imports_no_native_framework_at_module_scope():
    # AP-26: importing the module must not load Quartz/AppKit/pyobjc; the one
    # function that needs Quartz imports it lazily.
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(screen_access))
    top_level = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert not top_level & {"Quartz", "AppKit", "objc", "Foundation", "AVFoundation"}
