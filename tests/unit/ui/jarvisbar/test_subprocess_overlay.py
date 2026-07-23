"""SubprocessBarOverlay — the out-of-process bar surface (BUG-057 fix).

Drives the proxy against a fake Popen so every surface call's wire format,
the local mirrors (_mode/_persistent/_muted), the ready handshake, event →
callback dispatch, and the dead-host degrade are proven without spawning a
process. The real cross-process path is covered in test_host_protocol.py.
"""

from __future__ import annotations

import gc
import io
import json
import threading

import pytest

from jarvis.ui.jarvisbar import subprocess_overlay as mod
from jarvis.ui.jarvisbar.subprocess_overlay import (
    SubprocessBarOverlay,
    SubprocessMascotOverlay,
)


@pytest.fixture(autouse=True)
def _neutralize_pending_bar_respawns():
    """Guard against a fake-host artifact leaking a real subprocess spawn.

    Every fake host's scripted stdout is a finite ``io.StringIO`` that EOFs
    right after its scripted lines — read by the real event pump as "the
    host is gone" and, since BUG respawn support, bounded-scheduled onto its
    own background thread. That is harmless while THIS test's ``Popen`` fake
    is still monkeypatched in, but the thread's backoff can outlive the test:
    once monkeypatch reverts, a still-sleeping thread would call the REAL
    ``subprocess.Popen``. Mark every proxy created during the test as
    stopping once it ends so no pending respawn thread ever fires for real.
    """
    yield
    for obj in gc.get_objects():
        if isinstance(obj, SubprocessBarOverlay):
            obj._stopping = True


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
        "size_scale": 1.0,
    }
    assert surface._ready.is_set()  # scripted ready event consumed


def test_size_scale_rides_the_init_line_and_live_op(monkeypatch) -> None:
    # A custom boot size is carried on the init line so the host starts at the
    # right size; a later set_size_scale forwards a live-resize op and updates
    # the stored value (so a bounded respawn re-sends the latest size).
    surface, proc = _started_proxy(monkeypatch, size_scale=1.5)
    assert proc.sent()[0]["size_scale"] == 1.5
    surface.set_size_scale(0.8)
    assert proc.sent()[-1] == {"op": "set_size_scale", "scale": 0.8}
    assert surface._size_scale == 0.8


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
    assert surface._visible is False
    assert surface.release_startup_gate() is True
    assert surface._visible is True
    assert surface.release_startup_gate() is False  # released exactly once
    assert [m["op"] for m in proc.sent()[1:]] == ["release_startup_gate"]


def test_visibility_mirror_matches_bar_start_and_idle_semantics() -> None:
    assert SubprocessBarOverlay(persistent=True, startup_gated=False)._visible is True
    assert SubprocessBarOverlay(persistent=True, startup_gated=True)._visible is False
    non_persistent = SubprocessBarOverlay(persistent=False, startup_gated=False)
    assert non_persistent._visible is False
    non_persistent._send = lambda _msg: None  # type: ignore[method-assign]

    non_persistent.show("listen")
    assert non_persistent._visible is True
    non_persistent.show("idle")
    assert non_persistent._visible is False


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
    surface.set_on_talk(lambda: fired.append(("talk",)))
    surface.set_on_hangup(lambda: fired.append(("hangup",)))
    surface.set_on_mute_toggle(lambda: fired.append(("mute",)))
    surface.set_feedback_publisher(lambda k, d: fired.append(("feedback", k, d)))
    surface.set_on_show_window(lambda: (fired.append(("show_window",)), done.set()))

    events = (
        '{"event": "ready"}\n'
        '{"event": "talk"}\n'
        '{"event": "hangup"}\n'
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
    assert ("talk",) in fired
    assert ("hangup",) in fired
    assert ("mute",) in fired
    assert ("feedback", "bar", {"a": 1}) in fired
    assert ("show_window",) in fired


class _FakeSpeechPipeline:
    def __init__(self, *, session_active: bool) -> None:
        self.session_active = session_active
        self.talk_calls = 0
        self.hangup_calls = 0

    def is_session_active(self) -> bool:
        return self.session_active

    def request_voice_session(self) -> None:
        self.talk_calls += 1

    def request_hangup(self) -> None:
        self.hangup_calls += 1


def test_talk_event_executes_the_parent_pipeline(monkeypatch) -> None:
    pipeline = _FakeSpeechPipeline(session_active=False)
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline)
    surface = SubprocessBarOverlay()

    surface._dispatch_event({"event": "talk"})

    assert pipeline.talk_calls == 1
    assert pipeline.hangup_calls == 0


def test_hangup_event_closes_a_live_parent_session(monkeypatch) -> None:
    pipeline = _FakeSpeechPipeline(session_active=True)
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline)
    surface = SubprocessBarOverlay()

    surface._dispatch_event({"event": "hangup"})

    assert pipeline.hangup_calls == 1
    assert pipeline.talk_calls == 0


def test_hangup_event_escapes_a_stuck_active_parent_state(monkeypatch) -> None:
    pipeline = _FakeSpeechPipeline(session_active=False)
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline)
    surface = SubprocessBarOverlay()

    surface._dispatch_event({"event": "hangup"})

    assert pipeline.hangup_calls == 0
    assert pipeline.talk_calls == 1


def test_hangup_event_preserves_legacy_parent_pipeline_behavior(monkeypatch) -> None:
    class _LegacyPipeline:
        def __init__(self) -> None:
            self.hangup_calls = 0

        def request_hangup(self) -> None:
            self.hangup_calls += 1

    pipeline = _LegacyPipeline()
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline)
    surface = SubprocessBarOverlay()

    surface._dispatch_event({"event": "hangup"})

    assert pipeline.hangup_calls == 1


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


# --------------------------------------------------------------------- #
# Bounded auto-respawn (2026-07-18 revision — see module docstring)     #
# --------------------------------------------------------------------- #
class _SteadyStdout:
    """Stdout that blocks after "ready" instead of EOF-ing right away.

    The base ``_FakePopen.stdout`` is a finite ``io.StringIO`` that ends the
    instant its scripted lines are consumed — a fine stand-in for "the wire
    protocol works" tests, but it also means EVERY fake host "dies" (EOF)
    within a moment of spawning, which is exactly what the crash-loop tests
    below want. The respawn tests that need exactly ONE controlled death use
    this steady stream instead, so they are not racing the base fake's own
    built-in quick death.
    """

    def __init__(self, ready_line: str = '{"event": "ready"}\n') -> None:
        self._sent_ready = False
        self._ready_line = ready_line
        self._closed = threading.Event()

    def __iter__(self) -> _SteadyStdout:
        return self

    def __next__(self) -> str:
        if not self._sent_ready:
            self._sent_ready = True
            return self._ready_line
        self._closed.wait()  # block like a live process's open stdout pipe
        raise StopIteration

    def close(self) -> None:
        self._closed.set()


class _SteadyPopen(_FakePopen):
    """``_FakePopen`` whose stdout stays open until explicitly torn down."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.stdout = _SteadyStdout()

    def kill(self) -> None:
        super().kill()
        self.stdout.close()

    def wait(self, timeout: float | None = None) -> int:
        self.stdout.close()
        return super().wait(timeout=timeout)


def test_host_death_triggers_respawn_and_reapplies_desired_state(
    monkeypatch,
) -> None:
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(mod.subprocess, "Popen", _SteadyPopen)
    surface = SubprocessBarOverlay()
    surface.start_in_thread(timeout=2.0)
    proc = _FakePopen.last
    assert proc is not None

    surface.show("speak")
    surface.set_muted(True)
    surface.set_level(0.6)
    proc._returncode = 1  # the host died
    surface.set_level(0.9)  # a call against the dead proc triggers detection

    assert surface._respawn_succeeded.wait(timeout=2.0)
    new_proc = _FakePopen.last
    assert new_proc is not proc  # a fresh host process was spawned

    sent = new_proc.sent()
    assert sent[0] == {
        "op": "init",
        "persistent": True,
        "accent": "#e7c46e",
        "startup_gated": False,
        "size_scale": 1.0,
    }
    # Last known state (shown in "speak", muted, last level 0.9) re-applied.
    assert [m["op"] for m in sent[1:]] == ["show", "set_muted", "set_level"]
    assert sent[1] == {"op": "show", "mode": "speak"}
    assert sent[2] == {"op": "set_muted", "muted": True}
    assert sent[3] == {"op": "set_level", "level": 0.9}
    assert surface._respawn_attempts == 1
    surface.stop()


class _QuicklyDyingStdout:
    """Emits "ready", then EOFs after a brief REAL delay.

    ``mod.time.sleep`` gets monkeypatched to instant in these tests (so the
    5s production backoff doesn't slow them down) — but ``mod.time`` IS the
    same module object this test file imports, so a plain ``time.sleep(...)``
    in this fake would ALSO be neutered by that same monkeypatch.
    ``threading.Event().wait(timeout=...)`` uses its own timing and is
    unaffected, giving the spawning thread genuine wall-clock room to finish
    its own post-spawn bookkeeping (ready-wait, success log, reapply) before
    this stdout's EOF fires the next chained attempt — without it, multiple
    attempts race each other and can observe a torn ``self._proc``.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self._lines = iter(('{"event": "ready"}\n',))
        self._delay = delay
        self._settled = False

    def __iter__(self) -> _QuicklyDyingStdout:
        return self

    def __next__(self) -> str:
        try:
            return next(self._lines)
        except StopIteration:
            if not self._settled:
                self._settled = True
                threading.Event().wait(timeout=self._delay)
            raise


class _CrashLoopPopen(_FakePopen):
    """``_FakePopen`` that dies again a beat after "ready", not instantly."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.stdout = _QuicklyDyingStdout()


def test_respawn_is_bounded_to_three_attempts_then_gives_up(monkeypatch, caplog) -> None:
    """Every attempt uses the crash-prone fake, so each respawned host dies
    again (EOF) a beat later — chaining through all 3 attempts and proving
    the surface gives up instead of looping forever."""
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(mod.subprocess, "Popen", _CrashLoopPopen)
    surface = SubprocessBarOverlay()
    with caplog.at_level("WARNING", logger="jarvis.ui.jarvisbar"):
        surface.start_in_thread(timeout=2.0)
        assert surface._respawn_exhausted.wait(timeout=2.0)

    assert surface._respawn_attempts == 3
    assert "respawn attempts are spent" in caplog.text
    surface.stop()


def test_quick_respawn_death_still_consumes_an_attempt(monkeypatch) -> None:
    """A respawned host that dies again almost immediately must still count
    against the bound — otherwise a crash loop would spawn forever."""
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    spawned: list[_FakePopen] = []

    class _CountingPopen(_CrashLoopPopen):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            spawned.append(self)

    monkeypatch.setattr(mod.subprocess, "Popen", _CountingPopen)
    surface = SubprocessBarOverlay()
    surface.start_in_thread(timeout=2.0)
    assert surface._respawn_exhausted.wait(timeout=2.0)

    # Initial spawn + exactly 3 bounded respawns, each dying within its own
    # scripted EOF before the bound gave up — no unbounded crash loop.
    assert len(spawned) == 4
    surface.stop()


def test_surface_contract_exposes_every_bridge_method() -> None:
    from tests.unit.ui.jarvisbar.test_surface_contract import REQUIRED

    for name in REQUIRED:
        assert getattr(SubprocessBarOverlay, name, None) is not None, name
    # Same reset-path contract as NullOverlay: no _root instance attribute.
    assert not hasattr(SubprocessBarOverlay(), "_root")


# --------------------------------------------------------------------- #
# SubprocessMascotOverlay — the mascot flavor of the same host proxy    #
# --------------------------------------------------------------------- #
def _started_mascot(monkeypatch, **kwargs) -> tuple[SubprocessMascotOverlay, _FakePopen]:
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
    assert surface._visible is False  # sticky=False mascot starts withdrawn


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
        "size_scale": 1.0,
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
    # _SteadyPopen (not the base fake) so the host doesn't EOF and schedule a
    # respawn thread — this test only cares about the two spawn-time threads.
    monkeypatch.setattr(mod.subprocess, "Popen", _SteadyPopen)
    surface = SubprocessMascotOverlay()
    surface.start_in_thread(timeout=2.0)
    assert names == ["orb-host-events", "orb-host-stderr"]
    surface.stop()


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
