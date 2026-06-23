"""Tests for the runtime privileged-prompt probe (UAC Secure-Desktop & co.).

These cover the platform dispatch + defensive contract WITHOUT touching the real
Win32 path (headless-CI doctrine, mirrors test_system_cursor.py): the actual
``OpenInputDesktop`` probe is injected via the ``probe`` seam, and the live
Windows path is verified on a real desktop separately.
"""

from __future__ import annotations

import pytest

from jarvis.platform import privileged_prompt


@pytest.fixture(autouse=True)
def _display_present(monkeypatch):
    """Default every test to a host that HAS a display, so the headless guard
    does not short-circuit the dispatch under test. The headless case has its
    own explicit test that overrides this."""
    monkeypatch.setattr(
        "jarvis.platform.probes.display_present", lambda: True
    )


def test_active_when_probe_reports_true():
    assert privileged_prompt.privileged_prompt_active(probe=lambda: True) is True


def test_inactive_when_probe_reports_false():
    assert privileged_prompt.privileged_prompt_active(probe=lambda: False) is False


def test_inactive_when_probe_reports_unknown_none():
    # None = "could not determine" — must NEVER claim a prompt is up.
    assert privileged_prompt.privileged_prompt_active(probe=lambda: None) is False


def test_inactive_when_probe_raises():
    def _boom():
        raise OSError("win32 call failed")

    assert privileged_prompt.privileged_prompt_active(probe=_boom) is False


def test_inactive_on_platform_without_runtime_probe(monkeypatch):
    # macOS/Linux have no reliable dependency-free runtime probe yet → False
    # (the blank-frame heuristic at the capture site covers them).
    monkeypatch.setattr(
        "jarvis.platform.privileged_prompt.detect_platform", lambda: "linux"
    )
    assert privileged_prompt.privileged_prompt_active() is False


def test_inactive_when_headless_even_if_probe_true(monkeypatch):
    # No display → graceful no-op regardless of what a probe would say.
    monkeypatch.setattr(
        "jarvis.platform.probes.display_present", lambda: False
    )
    assert privileged_prompt.privileged_prompt_active(probe=lambda: True) is False


def test_windows_dispatch_selects_secure_desktop_probe(monkeypatch):
    # On Windows with no override, the real Secure-Desktop probe is selected.
    monkeypatch.setattr(
        "jarvis.platform.privileged_prompt.detect_platform", lambda: "win32"
    )
    sentinel = object()
    captured = {}

    def _fake_secure_desktop():
        captured["called"] = True
        return True

    monkeypatch.setattr(
        privileged_prompt, "_windows_secure_desktop_active", _fake_secure_desktop
    )
    assert privileged_prompt.privileged_prompt_active() is True
    assert captured.get("called") is True
    del sentinel
