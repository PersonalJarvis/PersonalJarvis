"""The macOS system-consent guard data and matcher (P9: an agent never answers a system dialog).

Pure data plus one matcher driven by an injected window list. The owner names are
UNVERIFIED (nobody can run macOS in the test sandbox); these tests pin the matching
rules, not what a real Mac names its dialogs.
"""

from __future__ import annotations

import os

import pytest

from jarvis.cu import system_dialogs as sd


def _window(
    owner: str,
    title: str = "",
    *,
    alpha: float = 1.0,
    size: tuple[int, int] = (400, 300),
    layer: int | None = None,
    pid: int | None = None,
):
    entry = {
        "kCGWindowOwnerName": owner,
        "kCGWindowName": title,
        "kCGWindowAlpha": alpha,
        "kCGWindowBounds": {"Width": size[0], "Height": size[1]},
    }
    if layer is not None:
        entry["kCGWindowLayer"] = layer
    if pid is not None:
        entry["kCGWindowOwnerPID"] = pid
    return entry


@pytest.mark.parametrize(
    "owner",
    [
        "UserNotificationCenter",
        "universalaccessd",
        "SecurityAgent",
        "coreautha",
        "authorizationhost",
    ],
)
def test_known_consent_owners_match_case_insensitively(owner):
    assert sd.is_system_consent_window(owner)
    assert sd.is_system_consent_window(owner.upper())


@pytest.mark.parametrize("owner", ["Safari", "Finder", "Terminal", "", "Personal Jarvis"])
def test_ordinary_apps_are_never_a_consent_surface(owner):
    assert not sd.is_system_consent_window(owner, "Privacy")


def test_system_settings_is_a_consent_surface_only_on_a_privacy_pane():
    assert sd.is_system_consent_window("System Settings", "Privacy & Security")
    assert sd.is_system_consent_window("System Settings", "Accessibility")
    # A mission that changes the wallpaper must still work.
    assert not sd.is_system_consent_window("System Settings", "Wallpaper")
    assert not sd.is_system_consent_window("System Settings", "")


def test_the_frontmost_consent_window_is_reported():
    windows = [_window("UserNotificationCenter", "Jarvis would like to..."), _window("Safari")]
    assert sd.frontmost_consent_owner(lambda: windows) == "UserNotificationCenter"


def test_chrome_and_invisible_windows_do_not_hide_a_dialog_behind_them():
    windows = [
        _window("Window Server", "Menubar"),
        _window("Dock", "Dock"),
        _window("Notification Center", "banner"),
        _window("Some Overlay", alpha=0.0),
        _window("Tiny Helper", size=(1, 1)),
        _window("SecurityAgent", "Authentication"),
        _window("Safari"),
    ]
    assert sd.frontmost_consent_owner(lambda: windows) == "SecurityAgent"


def test_an_ordinary_frontmost_window_decides_even_with_a_dialog_behind_it():
    # The dialog is not frontmost: the person already moved on, nothing to pause for.
    windows = [_window("Safari"), _window("SecurityAgent", "Authentication")]
    assert sd.frontmost_consent_owner(lambda: windows) == ""


def test_a_floating_overlay_of_another_app_does_not_mask_a_dialog_behind_it():
    # Front to back: a floating overlay (layer 3), the consent dialog (layer 8), Safari.
    windows = [
        _window("Python", layer=3, pid=999_999),
        _window("UserNotificationCenter", "Jarvis would like to...", layer=8),
        _window("Safari", layer=0),
    ]
    assert sd.frontmost_consent_owner(lambda: windows) == "UserNotificationCenter"


def test_jarvis_own_always_on_top_windows_do_not_mask_a_dialog_behind_them():
    own = os.getpid()
    windows = [
        _window("Personal Jarvis", "orb overlay", layer=3, pid=own),
        _window("Personal Jarvis", "virtual cursor", layer=3, pid=own),
        _window("Personal Jarvis", "Esc pill", layer=0, pid=own),  # even on layer 0
        _window("SecurityAgent", "Authentication", layer=8),
        _window("Safari", layer=0),
    ]
    assert sd.frontmost_consent_owner(lambda: windows) == "SecurityAgent"


def test_a_normal_window_of_another_app_in_front_ends_the_scan_even_with_overlays_above_it():
    windows = [
        _window("Python", layer=3, pid=999_999),
        _window("Safari", layer=0),
        _window("SecurityAgent", "Authentication", layer=8),
    ]
    assert sd.frontmost_consent_owner(lambda: windows) == ""


def test_a_malformed_layer_or_pid_fails_open_without_raising():
    windows = [
        _window("Safari", layer="not a number", pid="x"),  # type: ignore[arg-type]
        _window("SecurityAgent", "Authentication"),
    ]
    # An unreadable layer reads as a normal window: the scan stops there (fail open).
    assert sd.frontmost_consent_owner(lambda: windows) == ""
    windows = [
        _window("Python", pid="x"),  # type: ignore[arg-type]
        _window("SecurityAgent", "Authentication", layer=8),
    ]
    assert sd.frontmost_consent_owner(lambda: windows) == ""


def test_an_unreadable_window_list_fails_open_without_raising():
    def boom():
        raise RuntimeError("no window server")

    assert sd.frontmost_consent_owner(boom) == ""
    assert sd.frontmost_consent_owner(lambda: []) == ""


def test_the_texts_are_fixed_templates_with_the_stable_prefix():
    agent = sd.agent_detail()
    assert agent.startswith(f"[permission_needed:{sd.SYSTEM_DIALOG_BLOCK}] ")
    assert "must not try to answer" in agent and "must not retry" in agent
    assert "[permission_needed" not in sd.user_detail()
    assert "never answers system dialogs" in sd.user_detail()
