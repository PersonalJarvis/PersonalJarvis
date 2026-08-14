"""Non-darwin bar hosting: companion process by default, in-process fallback.

Regression anchor (2026-08-14): after a PC reboot the in-process bar froze for
~30 s and then rendered at ~0.3 fps while dictation itself worked fine — the
bar's Tk thread shares the GIL with wake models, STT and terminal restores,
and the 2026-07-10 stutter forensic proved no in-loop pacing can beat that
contention. The durable fix hosts the bar in the same companion process macOS
has used since BUG-057, on EVERY platform. These tests pin the selection
contract:

- default (``bar_out_of_process=True``): a live ``SubprocessBarOverlay``
- spawn failure / dead-on-arrival host: honest fallback to the proven
  in-process ``JarvisBarOverlay`` (never a silent no-op surface)
- opt-out (``bar_out_of_process=False``): the in-process bar directly

Seam-level: sys.platform is forced via monkeypatch (same pattern as
test_macos_ui_main_thread_gates.py), so this runs on any host OS.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jarvis.ui.desktop_app as desktop_app_module
from jarvis.ui.desktop_app import DesktopApp
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessBarOverlay


def _app(*, out_of_process: bool = True) -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)  # bypass heavy __init__
    app.cfg = SimpleNamespace(
        ui=SimpleNamespace(
            orb_style="jarvis_bar",
            bar_persistent=True,
            bar_accent="#e7c46e",
            orb_mascot_path="",
            bar_out_of_process=out_of_process,
        )
    )
    return app


def _fake_live_spawn(self: SubprocessBarOverlay, timeout: float = 3.0) -> None:
    # Stand-in for a healthy host: a poll()-able process object that reports
    # "still running", without ever spawning a real python -m ... child.
    self._proc = SimpleNamespace(poll=lambda: None)


def _no_tk(monkeypatch) -> None:
    monkeypatch.setattr(
        JarvisBarOverlay, "start_in_thread", lambda self, timeout=3.0: None
    )


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_bar_defaults_to_companion_process_host(monkeypatch, platform) -> None:
    monkeypatch.setattr(desktop_app_module.sys, "platform", platform)
    monkeypatch.setattr(SubprocessBarOverlay, "start_in_thread", _fake_live_spawn)

    surface = _app()._build_overlay_surface("jarvis_bar", gate_until_voice_ready=True)

    assert isinstance(surface, SubprocessBarOverlay)
    assert surface.host_alive is True
    # The boot gate must survive the trip into the proxy's init payload.
    assert surface._startup_gated is True
    assert surface._init_payload()["startup_gated"] is True


def test_bar_spawn_exception_falls_back_in_process(monkeypatch) -> None:
    monkeypatch.setattr(desktop_app_module.sys, "platform", "win32")
    _no_tk(monkeypatch)

    def _boom(self, timeout=3.0):
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(SubprocessBarOverlay, "start_in_thread", _boom)

    surface = _app()._build_overlay_surface("jarvis_bar")

    assert isinstance(surface, JarvisBarOverlay)


def test_bar_dead_on_arrival_host_falls_back_in_process(monkeypatch) -> None:
    # The proxy's spawn path degrades internally (cosmetic surface contract),
    # so a swallowed failure leaves no live process — host_alive is what the
    # builder checks before trusting the proxy.
    monkeypatch.setattr(desktop_app_module.sys, "platform", "linux")
    _no_tk(monkeypatch)
    monkeypatch.setattr(
        SubprocessBarOverlay, "start_in_thread", lambda self, timeout=3.0: None
    )

    surface = _app()._build_overlay_surface("jarvis_bar")

    assert isinstance(surface, JarvisBarOverlay)


def test_bar_opt_out_uses_in_process_bar_directly(monkeypatch) -> None:
    monkeypatch.setattr(desktop_app_module.sys, "platform", "win32")
    _no_tk(monkeypatch)
    spawned: list[bool] = []
    monkeypatch.setattr(
        SubprocessBarOverlay,
        "start_in_thread",
        lambda self, timeout=3.0: spawned.append(True),
    )

    surface = _app(out_of_process=False)._build_overlay_surface("jarvis_bar")

    assert isinstance(surface, JarvisBarOverlay)
    assert spawned == []  # the escape hatch must not even attempt a host


def test_host_alive_reflects_process_state() -> None:
    surface = SubprocessBarOverlay()
    assert surface.host_alive is False  # never spawned
    surface._proc = SimpleNamespace(poll=lambda: None)
    assert surface.host_alive is True  # running
    surface._proc = SimpleNamespace(poll=lambda: 1)
    assert surface.host_alive is False  # exited


def test_priority_raise_helper_never_raises() -> None:
    # Capability-gated scheduling nicety: the only contract is "bool out, no
    # exception, idempotent" — the actual grant depends on OS and privileges.
    from jarvis.core.process_utils import raise_own_priority_above_normal

    first = raise_own_priority_above_normal()
    second = raise_own_priority_above_normal()
    assert isinstance(first, bool)
    assert isinstance(second, bool)
