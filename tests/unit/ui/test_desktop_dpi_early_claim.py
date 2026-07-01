"""The desktop process claims DPI awareness BEFORE any window is created.

Regression guard for the recurring "JarvisBar shrank and jumped mid-session,
only a restart fixes it" bug. Root cause: the always-on bar's Tk window is built
(backend thread) while the process is still DPI-UNAWARE, so Windows upscales it
to a normal size; then pywebview's ``webview.start()`` calls
``SetProcessDPIAware()`` at RUNTIME, which strips that virtualization off the
already-existing window and it snaps to raw pixels. Claiming SYSTEM awareness at
desktop_app import — before the backend thread and the webview — makes
pywebview's later call a no-op, so the flip can never happen. This test fails if
that early claim is ever removed (e.g. clobbered by a consolidate snapshot).
"""
from __future__ import annotations

import ctypes
import types

import jarvis.ui.desktop_app as da


def test_claims_system_dpi_awareness_on_win32(monkeypatch):
    calls: list[str] = []
    fake_windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(SetProcessDPIAware=lambda: calls.append("set"))
    )
    monkeypatch.setattr(da.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)

    da._claim_system_dpi_awareness()

    assert calls == ["set"], (
        "the process must claim SYSTEM DPI awareness up front — else the bar is "
        "created unaware and pywebview's later flip shrinks + moves it"
    )


def test_dpi_claim_is_noop_off_windows(monkeypatch):
    # Cross-platform: off Windows there is no ``ctypes.windll`` — the claim must
    # return before touching it and must never raise.
    monkeypatch.setattr(da.sys, "platform", "linux")
    da._claim_system_dpi_awareness()


def test_dpi_claim_swallows_failure(monkeypatch):
    def _boom() -> None:
        raise OSError("no user32 here")

    fake_windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(SetProcessDPIAware=_boom)
    )
    monkeypatch.setattr(da.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)

    # Guarded — a DPI hiccup must never block boot.
    da._claim_system_dpi_awareness()
