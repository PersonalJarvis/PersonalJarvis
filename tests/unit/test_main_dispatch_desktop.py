"""Bare `jarvis` must launch the FULL Desktop App, never the tray-only loop.

Field report 2026-07-21: the website advertises `jarvis` as "full desktop:
window + voice + Orb overlay", but the console script's default was the
legacy standalone tray loop — no backend, no window, no voice — so the
terminal just sat there apparently loading forever. Bare `jarvis` now
delegates to the web launcher (what run.bat / run.sh / the installer start);
the tray loop stays reachable behind the explicit --tray-only diagnostic.
"""
from __future__ import annotations

import jarvis.__main__ as main_mod
import jarvis.ui.web.launcher as launcher_mod


def _capture_launcher(monkeypatch) -> list[list[str] | None]:
    calls: list[list[str] | None] = []

    def fake_main(argv: list[str] | None = None) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(launcher_mod, "main", fake_main)
    return calls


def test_bare_jarvis_launches_the_desktop_app(monkeypatch) -> None:
    calls = _capture_launcher(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")  # a headless-Linux CI host stays "desktop"
    assert main_mod.main([]) == 0
    assert calls == [[]]


def test_bare_jarvis_on_headless_linux_degrades_to_serve(monkeypatch, capsys) -> None:
    calls = _capture_launcher(monkeypatch)
    monkeypatch.setattr(main_mod.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert main_mod.main([]) == 0
    assert calls == [["--headless"]]
    assert "headless" in capsys.readouterr().out


def test_debug_flag_reaches_the_launcher_as_env(monkeypatch) -> None:
    calls = _capture_launcher(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("JARVIS_DEBUG", raising=False)
    assert main_mod.main(["--debug"]) == 0
    assert calls == [[]]
    import os

    assert os.environ.get("JARVIS_DEBUG") == "1"


def test_tray_only_flag_keeps_the_legacy_loop_reachable(monkeypatch) -> None:
    calls = _capture_launcher(monkeypatch)

    async def fake_tray(debug: bool = False) -> int:
        return 7

    monkeypatch.setattr(main_mod, "_run_tray_app", fake_tray)
    assert main_mod.main(["--tray-only"]) == 7
    assert calls == []  # the desktop launcher was NOT started
