"""Bar host process protocol (jarvis.ui.jarvisbar.host).

The host lets the bar's Tk mainloop run on a companion process's MAIN thread
(the only thread Aqua-Tk accepts on macOS, BUG-057). These tests drive the
dispatch/reader seams in-process with a recording bar, plus one real
cross-process round-trip using the echo double (JARVIS_BAR_HOST_FAKE=1) so
the pipes, EOF shutdown and ready handshake are proven without a display.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest

from jarvis.ui.jarvisbar import host


class _RecordingBar:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._persistent = True

    def show(self, mode: str = "listen") -> None:
        self.calls.append(("show", mode))

    def hide(self) -> None:
        self.calls.append(("hide",))

    def set_level(self, level: float) -> None:
        self.calls.append(("set_level", level))

    def set_muted(self, muted: bool) -> None:
        self.calls.append(("set_muted", muted))

    def release_startup_gate(self) -> bool:
        self.calls.append(("release_startup_gate",))
        return True

    def reassert_z_order(self) -> None:
        self.calls.append(("reassert_z_order",))

    def play_animation(self, name: str, **params) -> None:
        self.calls.append(("play_animation", name, params))

    def stop_animation(self, name: str) -> None:
        self.calls.append(("stop_animation", name))

    def show_listening_transcript(self, text: str = "", duration_ms: int = 0) -> None:
        self.calls.append(("show_listening_transcript", text, duration_ms))

    def hide_comment(self) -> None:
        self.calls.append(("hide_comment",))

    def start_mouth_animation(self, duration_ms: int = 0) -> None:
        self.calls.append(("start_mouth_animation", duration_ms))

    def stop_mouth_animation(self) -> None:
        self.calls.append(("stop_mouth_animation",))

    def _on_reset_double_click(self, _event=None) -> None:
        self.calls.append(("reset_position",))

    def stop(self) -> None:
        self.calls.append(("stop",))


def test_dispatch_maps_every_op_onto_the_surface() -> None:
    bar = _RecordingBar()
    ops = [
        {"op": "show", "mode": "listen"},
        {"op": "hide"},
        {"op": "set_level", "level": 0.5},
        {"op": "set_muted", "muted": True},
        {"op": "set_persistent", "enabled": False},
        {"op": "release_startup_gate"},
        {"op": "reassert_z_order"},
        {"op": "play_animation", "name": "wave", "params": {"x": 1}},
        {"op": "stop_animation", "name": "wave"},
        {"op": "show_listening_transcript", "text": "hi", "duration_ms": 9},
        {"op": "hide_comment"},
        {"op": "start_mouth_animation", "duration_ms": 7},
        {"op": "stop_mouth_animation"},
        {"op": "reset_position"},
    ]
    for msg in ops:
        assert host.dispatch(bar, msg) is True
    assert bar._persistent is False
    assert ("show", "listen") in bar.calls
    assert ("set_level", 0.5) in bar.calls
    assert ("set_muted", True) in bar.calls
    assert ("play_animation", "wave", {"x": 1}) in bar.calls
    assert ("show_listening_transcript", "hi", 9) in bar.calls
    assert ("reset_position",) in bar.calls


def test_dispatch_stop_returns_false_and_unknown_is_tolerated() -> None:
    bar = _RecordingBar()
    assert host.dispatch(bar, {"op": "stop"}) is False
    assert host.dispatch(bar, {"op": "definitely-not-a-real-op"}) is True


class _SparseSurface:
    """A surface missing several ops (like the mascot OrbOverlay)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def show(self, mode: str = "listen") -> None:
        self.calls.append(("show", mode))

    def stop(self) -> None:
        self.calls.append(("stop",))


def test_dispatch_missing_surface_method_is_a_noop() -> None:
    surface = _SparseSurface()
    # OrbOverlay lacks e.g. set_muted/release_startup_gate/reassert_z_order —
    # a missing method must degrade to a no-op, never raise.
    assert host.dispatch(surface, {"op": "set_muted", "muted": True}) is True
    assert host.dispatch(surface, {"op": "release_startup_gate"}) is True
    assert host.dispatch(surface, {"op": "reassert_z_order"}) is True
    assert surface.calls == []


def test_reader_loop_survives_ops_the_surface_lacks() -> None:
    surface = _SparseSurface()
    stream = io.StringIO(
        '{"op": "set_muted", "muted": true}\n{"op": "show", "mode": "idle"}\n'
    )
    host.reader_loop(surface, stream)
    assert ("show", "idle") in surface.calls
    assert surface.calls[-1] == ("stop",)


def test_build_surface_mascot_builds_an_orb_overlay(monkeypatch) -> None:
    from ui.orb.overlay import OrbOverlay

    monkeypatch.delenv("JARVIS_BAR_HOST_FAKE", raising=False)
    monkeypatch.setattr(host, "_hide_dock_icon", lambda: None)
    surface = host._build_surface(
        {"op": "init", "surface": "mascot", "mascot_path": None}
    )
    assert isinstance(surface, OrbOverlay)


def test_build_surface_defaults_to_the_jarvis_bar(monkeypatch) -> None:
    """Regression: an init line without "surface" still builds the bar."""
    from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay

    monkeypatch.delenv("JARVIS_BAR_HOST_FAKE", raising=False)
    monkeypatch.setattr(host, "_hide_dock_icon", lambda: None)
    surface = host._build_surface(
        {"op": "init", "persistent": False, "accent": "#123456"}
    )
    assert isinstance(surface, JarvisBarOverlay)
    assert surface._persistent is False
    assert surface._accent == "#123456"


def test_reader_loop_survives_junk_and_stops_bar_on_eof() -> None:
    bar = _RecordingBar()
    stream = io.StringIO(
        "\n"
        "this is not json\n"
        '{"op": "show", "mode": "speak"}\n'
        '{"op": "set_level", "level": 1.0}\n'
    )
    host.reader_loop(bar, stream)  # hard_exit stays None in tests
    assert ("show", "speak") in bar.calls
    assert ("set_level", 1.0) in bar.calls
    assert bar.calls[-1] == ("stop",)  # EOF → the bar is stopped


def test_reader_loop_stop_command_stops_bar_without_draining_rest() -> None:
    bar = _RecordingBar()
    stream = io.StringIO('{"op": "stop"}\n{"op": "hide"}\n')
    host.reader_loop(bar, stream)
    assert ("hide",) not in bar.calls
    assert bar.calls[-1] == ("stop",)


def test_one_failing_command_does_not_kill_the_loop() -> None:
    class _ExplodingBar(_RecordingBar):
        def hide(self) -> None:  # type: ignore[override]
            raise RuntimeError("boom")

    bar = _ExplodingBar()
    stream = io.StringIO('{"op": "hide"}\n{"op": "show", "mode": "idle"}\n')
    host.reader_loop(bar, stream)
    assert ("show", "idle") in bar.calls


@pytest.mark.integration
def test_host_process_round_trip_with_echo_bar() -> None:
    """Real subprocess: init → ready, op echo, clean stop-driven exit."""
    env = dict(os.environ)
    env["JARVIS_BAR_HOST_FAKE"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "jarvis.ui.jarvisbar.host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        env=env,
        cwd=os.getcwd(),
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write('{"op": "init", "persistent": true}\n')
        proc.stdin.flush()
        # Wait for the ready handshake before sending ops — otherwise the
        # stop below can end the host before the daemon ready-announce
        # thread ever gets to emit its event.
        ready = json.loads(proc.stdout.readline())
        assert ready.get("event") == "ready"
        proc.stdin.write('{"op": "show", "mode": "listen"}\n')
        proc.stdin.write('{"op": "stop"}\n')
        proc.stdin.flush()
        out, _err = proc.communicate(timeout=30)
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        echoed = [e for e in events if e.get("event") == "op"]
        assert any(
            e.get("op") == "show" and e.get("args") == ["listen"] for e in echoed
        )
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.integration
def test_host_process_round_trip_with_mascot_surface() -> None:
    """Real subprocess, surface "mascot": forwarded ops reach the surface."""
    env = dict(os.environ)
    env["JARVIS_BAR_HOST_FAKE"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "jarvis.ui.jarvisbar.host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        env=env,
        cwd=os.getcwd(),
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(
            '{"op": "init", "surface": "mascot", "mascot_path": "assets/m.png"}\n'
        )
        proc.stdin.flush()
        # Wait for the ready handshake before sending ops — otherwise the
        # stop below can end the host before the daemon ready-announce
        # thread ever gets to emit its event.
        ready = json.loads(proc.stdout.readline())
        assert ready.get("event") == "ready"
        proc.stdin.write(
            '{"op": "play_animation", "name": "wave", "params": {"x": 1}}\n'
        )
        proc.stdin.write(
            '{"op": "show_listening_transcript", "text": "hi", "duration_ms": 9}\n'
        )
        proc.stdin.write('{"op": "stop"}\n')
        proc.stdin.flush()
        out, _err = proc.communicate(timeout=30)
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        echoed = [e for e in events if e.get("event") == "op"]
        assert any(
            e.get("op") == "play_animation"
            and e.get("args") == ["wave"]
            and e.get("kwargs") == {"x": 1}
            for e in echoed
        )
        assert any(
            e.get("op") == "show_listening_transcript"
            and e.get("args") == ["hi", 9]
            for e in echoed
        )
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
