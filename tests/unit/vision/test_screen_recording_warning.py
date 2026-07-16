"""H1: a denied macOS Screen-Recording grant must surface a clear English
onboarding message at the screenshot capture point, instead of silently
capturing the desktop wallpaper (which would make Computer-Use click blind).

These force the grant state via the permission-port seam — no real macOS
needed. Seam-level only: this proves uncached fail-closed behavior, not the
native CGPreflightScreenCaptureAccess implementation.
"""
from __future__ import annotations

import logging

from jarvis.vision import screenshot

_MSG = "Screen Recording permission not granted"


def _permission_port(monkeypatch, states):
    remaining = list(states)

    class _Port:
        current = None

        def state(self, _permission_id):
            return self.current

        def runtime_access_granted(self, _permission_id):
            from jarvis.platform.permissions import PermissionState

            self.current = remaining.pop(0)
            return self.current in {
                PermissionState.GRANTED,
                PermissionState.NOT_REQUIRED,
            }

    port = _Port()
    monkeypatch.setattr(
        "jarvis.platform.permissions.get_system_permission_port",
        lambda: port,
    )
    return remaining


def test_warns_once_when_screen_recording_denied(monkeypatch, caplog):
    from jarvis.platform.permissions import PermissionState

    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    remaining = _permission_port(
        monkeypatch,
        [PermissionState.NOT_GRANTED, PermissionState.NOT_GRANTED],
    )
    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is True
        assert screenshot.warn_if_screen_recording_denied() is True
    assert caplog.text.count(_MSG) == 1
    assert remaining == [], "the native state must be probed on both calls"


def test_no_warning_when_granted(monkeypatch, caplog):
    from jarvis.platform.permissions import PermissionState

    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    _permission_port(monkeypatch, [PermissionState.GRANTED])
    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is False
    assert _MSG not in caplog.text


def test_unavailable_native_probe_fails_closed(monkeypatch, caplog):
    from jarvis.platform.permissions import PermissionState

    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    _permission_port(monkeypatch, [PermissionState.UNAVAILABLE])
    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is True
    assert _MSG in caplog.text


def test_permission_recovery_is_observed_without_restart(monkeypatch, caplog):
    from jarvis.platform.permissions import PermissionState

    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    _permission_port(
        monkeypatch,
        [PermissionState.NOT_GRANTED, PermissionState.GRANTED],
    )

    with caplog.at_level(logging.INFO):
        assert screenshot.warn_if_screen_recording_denied() is True
        assert screenshot.warn_if_screen_recording_denied() is False

    assert "available again" in caplog.text
