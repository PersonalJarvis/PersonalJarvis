"""M5: the tray must degrade to a logged no-op (not a silently dying daemon
thread) on a box without a graphical display / notification-area host, and its
menu strings must be English (Output-Language Policy).

Seam-level: display_present is forced via monkeypatch — proven on this Windows
host without a real Linux/headless session.
"""
from __future__ import annotations

import logging
from pathlib import Path

from jarvis.ui import tray as tray_mod
from jarvis.ui.tray import JarvisTray


def test_tray_start_is_noop_without_display(monkeypatch, caplog) -> None:
    monkeypatch.setattr(tray_mod, "display_present", lambda: False, raising=False)
    # If the gate works, _run is never reached; the no-op keeps RED from launching
    # a real pystray icon.
    monkeypatch.setattr(JarvisTray, "_run", lambda self: None)
    t = JarvisTray()
    with caplog.at_level(logging.INFO):
        t.start()
    assert t._thread is None  # gated: no tray thread spawned
    assert "tray not started" in caplog.text.lower()


def test_tray_start_spawns_thread_with_display(monkeypatch) -> None:
    # AD-7: with a display present (Windows/macOS/Linux-X11) the tray still starts.
    monkeypatch.setattr(tray_mod, "display_present", lambda: True, raising=False)
    ran: list[bool] = []
    monkeypatch.setattr(JarvisTray, "_run", lambda self: ran.append(True))
    t = JarvisTray()
    t.start()
    if t._thread is not None:
        t._thread.join(timeout=2)
    assert ran == [True]


def test_tray_menu_strings_are_english() -> None:
    src = Path(tray_mod.__file__).read_text(encoding="utf-8")
    for german in (
        '"Öffnen"',
        '"Pausieren"',
        '"Fortsetzen"',
        '"Beenden"',
        '"Notfall-Stop"',
        '"Config neu laden"',
    ):
        assert german not in src, german
    for english in (
        '"Open"',
        '"Pause"',
        '"Resume"',
        '"Quit"',
        '"Emergency stop"',
        '"Reload config"',
    ):
        assert english in src, english
