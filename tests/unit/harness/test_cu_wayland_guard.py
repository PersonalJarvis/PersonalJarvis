"""Wayland honest-refusal guard for Computer-Use (audit 🔴 #8).

Wayland blocks global screen capture and input injection by design, and no
portal/libei/uinput backend is built yet — so CU would capture a black frame and
click nowhere. The loop refuses cleanly on a Wayland session instead of acting
blind. Windows / macOS / Linux-X11 are unaffected (is_wayland() is False).
"""
from __future__ import annotations

from jarvis.harness import screenshot_only_loop as sol


def test_block_message_on_wayland(monkeypatch):
    monkeypatch.setattr("jarvis.platform.probes.is_wayland", lambda: True)
    msg = sol._wayland_block_message()
    assert msg is not None
    assert "wayland" in msg.lower()
    assert "x11" in msg.lower()  # tells the user the way out


def test_no_block_off_wayland(monkeypatch):
    monkeypatch.setattr("jarvis.platform.probes.is_wayland", lambda: False)
    assert sol._wayland_block_message() is None


def test_block_message_never_raises(monkeypatch):
    def _boom() -> bool:
        raise RuntimeError("probe failed")

    monkeypatch.setattr("jarvis.platform.probes.is_wayland", _boom)
    assert sol._wayland_block_message() is None  # a probe error must not block
