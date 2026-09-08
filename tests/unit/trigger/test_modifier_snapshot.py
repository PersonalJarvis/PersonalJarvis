"""``modifier_snapshot`` — the live modifier probe the in-app recorder polls.

GitHub #98: macOS WKWebView often delivers a modifier keydown and then never
the matching keyup, so a recorder that only trusts DOM events hangs with
Option/Command still marked as held. The probe asks the OS instead. Empty
frozenset = "we can tell, and nothing is down"; None = "this host cannot tell".
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from jarvis.trigger.hotkey import modifier_snapshot


def test_win32_snapshot_reports_each_held_modifier(monkeypatch) -> None:
    from jarvis.trigger.backends import global_hotkeys as ghb

    down = {0x11, 0x5B}  # VK_CONTROL + VK_LWIN
    monkeypatch.setattr(ghb, "_async_key_is_down", lambda vk: int(vk) in down)

    tokens, reason = modifier_snapshot(platform="win32")
    assert tokens == frozenset({"ctrl", "win"})
    assert reason == ""


def test_win32_snapshot_empty_set_means_nothing_held(monkeypatch) -> None:
    from jarvis.trigger.backends import global_hotkeys as ghb

    monkeypatch.setattr(ghb, "_async_key_is_down", lambda vk: False)

    tokens, reason = modifier_snapshot(platform="win32")
    assert tokens == frozenset()
    assert reason == ""


def test_win32_snapshot_is_unknown_when_the_probe_cannot_run(monkeypatch) -> None:
    from jarvis.trigger.backends import global_hotkeys as ghb

    monkeypatch.setattr(ghb, "_async_key_is_down", lambda vk: None)

    tokens, reason = modifier_snapshot(platform="win32")
    assert tokens is None
    assert reason
    assert "on-screen" in reason.lower()


def test_darwin_snapshot_reads_the_flags_word(monkeypatch) -> None:
    fake = SimpleNamespace(
        kCGEventSourceStateCombinedSessionState=0,
        CGEventSourceFlagsState=lambda _state: (1 << 19) | (1 << 20),  # alt + cmd
    )
    monkeypatch.setitem(sys.modules, "Quartz", fake)

    tokens, reason = modifier_snapshot(platform="darwin")
    assert tokens == frozenset({"alt", "cmd"})
    assert reason == ""


def test_darwin_snapshot_is_unknown_without_quartz(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "Quartz", None)

    tokens, reason = modifier_snapshot(platform="darwin")
    assert tokens is None
    assert "Quartz" in reason


def test_linux_uses_the_live_listener_held_set() -> None:
    tokens, reason = modifier_snapshot(platform="linux", backend_held={"ctrl", "j", "alt_r", "cmd"})
    assert tokens == frozenset({"ctrl", "alt", "win"})
    assert reason == ""


def test_linux_without_a_listener_is_unknown(monkeypatch) -> None:
    import jarvis.platform.probes as probes

    monkeypatch.setattr(probes, "is_wayland", lambda: False)
    tokens, reason = modifier_snapshot(platform="linux")
    assert tokens is None
    assert reason


def test_wayland_names_the_compositor_instead_of_pretending(monkeypatch) -> None:
    import jarvis.platform.probes as probes

    monkeypatch.setattr(probes, "is_wayland", lambda: True)
    tokens, reason = modifier_snapshot(platform="linux")
    assert tokens is None
    assert "Wayland" in reason


def test_unknown_host_is_honest() -> None:
    tokens, reason = modifier_snapshot(platform="haiku")
    assert tokens is None
    assert reason
