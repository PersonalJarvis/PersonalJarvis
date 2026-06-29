"""CU works on the MAIN monitor: move the target window to primary at mission
start (audit G8c).

``_ensure_target_on_primary`` runs once before the first observe when
monitor="primary": if the foreground window is on a secondary screen it is moved
to the primary, and a note steers the model to re-read the freshly-captured main
screen. Honest no-op when already on primary, when the rect can't be read
(macOS/Linux today), or when the move is refused (Wayland) — never a blind act.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.harness import screenshot_only_loop as sol
from jarvis.platform import monitors as mon
from jarvis.platform import window_state as ws

# The harness conftest autouse-stubs sol._ensure_target_on_primary to a no-op so
# loop tests never move a real window. THIS file tests the real function, so we
# capture its reference at import time (before any fixture patches the module).
_ensure_target_on_primary = sol._ensure_target_on_primary

_VIRT = {"left": -2560, "top": 0, "width": 6400, "height": 2160}
_LEFT = {"left": -2560, "top": 0, "width": 2560, "height": 1440, "name": "left"}
_MAIN = {"left": 0, "top": 0, "width": 3840, "height": 2160, "name": "main"}


class _FakeSct:
    def __init__(self, mons: list) -> None:
        self.monitors = mons

    def __enter__(self) -> _FakeSct:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


def _patch_env(monkeypatch, *, rect, fg=True, move_result=(True, "moved App")):
    import mss

    monkeypatch.setattr(mss, "mss", lambda: _FakeSct([_VIRT, _LEFT, _MAIN]))
    monkeypatch.setattr(mon, "native_primary_origin", lambda: None)  # -> (0,0) = MAIN
    monkeypatch.setattr(
        ws, "foreground_window",
        lambda: ws.WindowInfo(title="App", handle=1) if fg else None,
    )
    monkeypatch.setattr(ws, "window_rect", lambda _w: rect)
    seen: dict = {}

    def _move(_w, p):
        seen["primary"] = p
        return move_result

    monkeypatch.setattr(ws, "move_window_to_primary", _move)
    return seen


def _ctx(monitor: str = "primary") -> SimpleNamespace:
    return SimpleNamespace(monitor=monitor, main_monitor="primary")


def test_skips_when_not_primary_mode():
    # monitor="foreground" -> the whole hook is a no-op (never touches windows).
    assert _ensure_target_on_primary(_ctx(monitor="foreground")) is None


def test_no_move_when_already_on_primary(monkeypatch):
    seen = _patch_env(monkeypatch, rect=(100, 100, 800, 600))  # center on MAIN
    assert _ensure_target_on_primary(_ctx()) is None
    assert "primary" not in seen  # move_window_to_primary never called


def test_moves_when_window_is_on_secondary(monkeypatch):
    seen = _patch_env(monkeypatch, rect=(-2000, 200, 800, 600))  # center on LEFT
    note = _ensure_target_on_primary(_ctx())
    assert note is not None
    assert "main monitor" in note.lower()
    assert seen["primary"]["name"] == "main"  # moved onto the resolved primary


def test_none_when_no_foreground_window(monkeypatch):
    _patch_env(monkeypatch, rect=(-2000, 200, 800, 600), fg=False)
    assert _ensure_target_on_primary(_ctx()) is None


def test_none_when_rect_unreadable(monkeypatch):
    _patch_env(monkeypatch, rect=None)  # macOS/Linux today
    assert _ensure_target_on_primary(_ctx()) is None


def test_honest_note_when_move_refused_and_stuck_on_secondary(monkeypatch):
    # Off-primary AND the move is refused (e.g. Wayland / an owned window): be
    # HONEST (G8c Part 3) — return a note that tells the model the window is stuck
    # on a secondary screen, instead of silently grounding on the empty primary.
    _patch_env(
        monkeypatch, rect=(-2000, 200, 800, 600),
        move_result=(False, "Wayland refusal"),
    )
    note = _ensure_target_on_primary(_ctx())
    assert note is not None
    assert "could not move" in note.lower()
    assert "secondary" in note.lower() or "drag" in note.lower()


def test_foreground_window_non_windows_is_title_only(monkeypatch):
    monkeypatch.setattr(ws, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ws, "get_foreground_title", lambda: "Some App")
    win = ws.foreground_window()
    assert win is not None
    assert win.title == "Some App"
    assert win.handle is None
