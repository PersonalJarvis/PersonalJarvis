"""H1: a denied macOS Screen-Recording grant must surface a clear English
onboarding message at the screenshot capture point, instead of silently
capturing the desktop wallpaper (which would make Computer-Use click blind).

These force the grant state via the platform probe seam — no real macOS needed.
Seam-level only: this proves the warn-once decision logic, NOT that the real
CGPreflightScreenCaptureAccess call behaves as assumed on a real Mac.
"""
from __future__ import annotations

import logging

from jarvis.vision import screenshot

_MSG = "Screen Recording permission not granted"


def test_warns_once_when_screen_recording_denied(monkeypatch, caplog):
    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    monkeypatch.setattr(
        "jarvis.platform.probes.screen_recording_granted", lambda: False
    )
    with caplog.at_level(logging.WARNING):
        # darwin + explicitly denied -> capture will be blank: returns True.
        assert screenshot.warn_if_screen_recording_denied() is True
        # Called again (every frame) -> still blank, but warns only ONCE.
        assert screenshot.warn_if_screen_recording_denied() is True
    assert caplog.text.count(_MSG) == 1


def test_no_warning_when_granted(monkeypatch, caplog):
    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    monkeypatch.setattr(
        "jarvis.platform.probes.screen_recording_granted", lambda: True
    )
    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is False
    assert _MSG not in caplog.text


def test_no_warning_when_unknown(monkeypatch, caplog):
    # None = pyobjc-Quartz absent: we cannot prove denial, so do NOT nag.
    monkeypatch.setattr(screenshot, "_screen_recording_warned", False)
    monkeypatch.setattr(
        "jarvis.platform.probes.screen_recording_granted", lambda: None
    )
    with caplog.at_level(logging.WARNING):
        assert screenshot.warn_if_screen_recording_denied() is False
    assert _MSG not in caplog.text
