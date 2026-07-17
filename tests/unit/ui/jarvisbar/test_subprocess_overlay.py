"""SubprocessBarOverlay — the out-of-process bar surface (BUG-057 fix).

Drives the proxy against a fake Popen so every surface call's wire format,
the local mirrors (_mode/_persistent/_muted), the ready handshake, event →
callback dispatch, and the dead-host degrade are proven without spawning a
process. The real cross-process path is covered in test_host_protocol.py.
"""
from __future__ import annotations

import io
import json
import threading

from jarvis.ui.jarvisbar import subprocess_overlay as mod
from jarvis.ui.jarvisbar.subprocess_overlay import (
    SubprocessBarOverlay,
    SubprocessMascotOverlay,
)


class _FakeStdin:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None: ...

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    """Records stdin writes; serves scripted stdout events; never a process."""

    last: _FakePopen | None = None

    def __init__(self, *_args, **_kwargs) -> None:
        self.stdin = _FakeStdin()
        self.stdout = io.StringIO('{"event": "ready"}\n')
        self.stderr = io.StringIO("")
        self._returncode: int | None = None
        _FakePopen.last = self

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self._returncode = 0
        return 0

    def kill(self) -> None:
        self._returncode = -9

    def sent(self) -> list[dict]:
        return [json.loads(line) for line in self.stdin.lines]


def _started_proxy(monkeypatch, **kwargs) -> tuple[SubprocessBarOverlay, _FakePopen]:
    monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)
    surface = SubprocessBarOverlay(**kwargs)
    surface.start_in_thread(timeout=2.0)
    proc = _FakePopen.last
    assert proc is not None
    return surface, proc


def test_init_line_carries_the_constructor_config(monkeypatch) -> None:
    surface, proc = _started_proxy(
        monkeypatch, persistent=False, accent="#123456", startup_gated=True
    )
    init = proc.sent()[0]
    assert init == {
        "op": "init",
        "persistent": False,
        "accent": "#123456",
        "startup_gated": True,
    }
    assert surface._ready.is_set()  # scripted ready event consumed


def test_surface_calls_become_wire_ops_and_mirror_locally(monkeypatch) -> None:
    surface, proc = _started_proxy(monkeypatch)
    surface.show("speak")
    surface.show("bogus")  # invalid mode → ignored, not sent
    surface.hide()
    surface.set_level(0.75)
    surface.set_muted(True)
    surface.reassert_z_order()
    surface._on_reset_double_click()
    ops = [m["op"] for m in proc.sent()[1:]]
    assert ops == [
        "show",
        "hide",
        "set_level",
        "set_muted",
        "reassert_z_order",
        "reset_position",
    ]
    assert surface._mode == "speak"
    assert surface._muted is True


def test_text_and_mouth_methods_are_local_noops(monkeypatch) -> None:
    surface, proc = _started_proxy(monkeypatch)
    surface.play_animation("wave", x=1)
    surface.stop_animation("wave")
    surface.show_listening_transcript("hi", 5)
    surface.hide_comment()
    surface.start_mouth_animation(5)
    surface.stop_mouth_animation()
    assert len(proc.sent()) == 1  # only the init line went out


def test_release_startup_gate_semantics_match_the_real_bar(monkeypatch) -> None:
    surface, proc = _started_proxy(monkeypatch, startup_gated=True)
    assert surface.release_startup_gate() is True
    assert surface.release_startup_gate() is False  # released exactly once
    assert [m["op"] for m in proc.sent()[1:]] == ["release_startup_gate"]


def test_persistent_attribute_flip_is_forwarded(monkeypatch) -> None:
    surface, proc = _started_proxy(monkeypatch, persistent=True)
    surface._persistent = False  # the set_bar_persistent live-flip contract
    assert surface._persistent is False
    flips = [m for m in proc.sent() if m["op"] == "set_persistent"]
    assert flips == [{"op": "set_persistent", "enabled": False}]


def test_child_events_dispatch_to_bridge_callbacks(monkeypatch) -> None:
    monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)
    surface = SubprocessBarOverlay()
    fired: list[tuple] = []
    done = threading.Event()
    surface.set_on_mute_toggle(lambda: fired.append(("mute",)))
    surface.set_feedback_publisher(lambda k, d: fired.append(("feedback", k, d)))
    surface.set_on_show_window(lambda: (fired.append(("show_window",)), done.set()))

    events = (
        '{"event": "ready"}\n'
        '{"event": "mute_toggle"}\n'
        '{"event": "feedback", "kind": "bar", "payload": {"a": 1}}\n'
        "not json\n"
        '{"event": "show_window"}\n'
    )

    class _EventPopen(_FakePopen):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.stdout = io.StringIO(events)

    monkeypatch.setattr(mod.subprocess, "Popen", _EventPopen)
    surface.start_in_thread(timeout=2.0)
    assert done.wait(timeout=2.0)
    assert ("mute",) in fired
    assert ("feedback", "bar", {"a": 1}) in fired
    assert ("show_window",) in fired


def test_dead_host_degrades_to_noop_without_raising(monkeypatch) -> None:
    surface, proc = _started_proxy(monkeypatch)
    proc._returncode = 1  # the host died
    surface.show("listen")  # must not raise
    surface.set_level(0.3)
    assert [m["op"] for m in proc.sent()[1:]] == []  # nothing reached the wire


def test_spawn_failure_leaves_a_noop_surface(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise OSError("no python")

    monkeypatch.setattr(mod.subprocess, "Popen", _boom)
    surface = SubprocessBarOverlay()
    surface.start_in_thread(timeout=0.1)
    surface.show("listen")  # every call is a safe no-op
    surface.stop()


def test_stop_sends_stop_closes_stdin_and_waits(monkeypatch) -> None:
    surface, proc = _started_proxy(monkeypatch)
    surface.stop()
    assert proc.sent()[-1] == {"op": "stop"}
    assert proc.stdin.closed is True
    assert surface._proc is None


def test_surface_contract_exposes_every_bridge_method() -> None:
    from tests.unit.ui.jarvisbar.test_surface_contract import REQUIRED

    for name in REQUIRED:
        assert getattr(SubprocessBarOverlay, name, None) is not None, name
    # Same reset-path contract as NullOverlay: no _root instance attribute.
    assert not hasattr(SubprocessBarOverlay(), "_root")


# --------------------------------------------------------------------- #
# SubprocessMascotOverlay — the mascot flavor of the same host proxy    #
# --------------------------------------------------------------------- #
def _started_mascot(
    monkeypatch, **kwargs
) -> tuple[SubprocessMascotOverlay, _FakePopen]:
    monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)
    surface = SubprocessMascotOverlay(**kwargs)
    surface.start_in_thread(timeout=2.0)
    proc = _FakePopen.last
    assert proc is not None
    return surface, proc


def test_mascot_init_line_declares_surface_and_mascot_path(monkeypatch) -> None:
    surface, proc = _started_mascot(monkeypatch, mascot_path="assets/m.png")
    init = proc.sent()[0]
    assert init == {"op": "init", "surface": "mascot", "mascot_path": "assets/m.png"}
    assert surface._ready.is_set()  # scripted ready event consumed


def test_bar_init_payload_is_unchanged_by_the_mascot_variant(monkeypatch) -> None:
    """Regression: the bar proxy's init line keeps its exact pre-mascot shape."""
    surface, proc = _started_proxy(
        monkeypatch, persistent=True, accent="#e7c46e", startup_gated=False
    )
    assert proc.sent()[0] == {
        "op": "init",
        "persistent": True,
        "accent": "#e7c46e",
        "startup_gated": False,
    }
    assert not isinstance(surface, SubprocessMascotOverlay)


def test_mascot_forwards_text_and_mouth_ops_over_the_wire(monkeypatch) -> None:
    surface, proc = _started_mascot(monkeypatch)
    surface.play_animation("wave", x=1)
    surface.stop_animation("wave")
    surface.show_listening_transcript("hi", 5)
    surface.hide_comment()
    surface.start_mouth_animation(5)
    surface.stop_mouth_animation()
    sent = proc.sent()[1:]
    assert sent == [
        {"op": "play_animation", "name": "wave", "params": {"x": 1}},
        {"op": "stop_animation", "name": "wave"},
        {"op": "show_listening_transcript", "text": "hi", "duration_ms": 5},
        {"op": "hide_comment"},
        {"op": "start_mouth_animation", "duration_ms": 5},
        {"op": "stop_mouth_animation"},
    ]


def test_mascot_pump_threads_use_orb_host_names(monkeypatch) -> None:
    names: list[str | None] = []
    real_thread = mod.threading.Thread

    class _NamedThread(real_thread):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs) -> None:
            names.append(kwargs.get("name"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(mod.threading, "Thread", _NamedThread)
    _started_mascot(monkeypatch)
    assert names == ["orb-host-events", "orb-host-stderr"]


def test_mascot_dead_host_degrades_to_noop_without_raising(monkeypatch) -> None:
    surface, proc = _started_mascot(monkeypatch)
    proc._returncode = 1  # the host died
    surface.play_animation("wave")  # must not raise
    surface.show_listening_transcript("hi", 5)
    assert proc.sent()[1:] == []  # nothing reached the wire


def test_mascot_surface_contract_matches_the_bar_proxy() -> None:
    from tests.unit.ui.jarvisbar.test_surface_contract import REQUIRED

    for name in REQUIRED:
        assert getattr(SubprocessMascotOverlay, name, None) is not None, name
    # Same reset-path contract as NullOverlay: no _root instance attribute.
    assert not hasattr(SubprocessMascotOverlay(), "_root")
