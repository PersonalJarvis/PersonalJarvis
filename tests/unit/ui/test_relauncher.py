"""Unit tests for the detached self-restart helper (jarvis.ui.relauncher).

The wait loop, command construction, and argv handling are pure and injectable,
so they are tested without spawning real processes.
"""
from __future__ import annotations

from jarvis.ui import relauncher


def test_build_launch_command():
    assert relauncher.build_launch_command("py.exe") == [
        "py.exe",
        "-m",
        "jarvis.ui.web.launcher",
    ]


def test_wait_returns_true_once_pid_gone():
    # alive for the first 2 polls, gone on the 3rd.
    calls = {"n": 0}

    def alive(_pid):
        calls["n"] += 1
        return calls["n"] < 3

    clock = {"t": 0.0}
    out = relauncher.wait_for_pid_exit(
        1234,
        timeout=10.0,
        poll=0.1,
        _alive=alive,
        _now=lambda: clock["t"],
        _sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    assert out is True
    assert calls["n"] == 3


def test_wait_times_out_when_pid_never_dies():
    clock = {"t": 0.0}
    out = relauncher.wait_for_pid_exit(
        1234,
        timeout=1.0,
        poll=0.5,
        _alive=lambda _pid: True,
        _now=lambda: clock["t"],
        _sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    assert out is False


def test_main_waits_then_spawns_fresh_launcher():
    spawned: list[dict] = []

    def fake_spawn(cmd, **kwargs):
        spawned.append({"cmd": cmd, "kwargs": kwargs})
        return object()

    waited: list[int] = []
    rc = relauncher.main(
        ["4242", r"C:\repo"],
        _wait=lambda pid, **_kw: waited.append(pid) or True,
        _spawn=fake_spawn,
        _sleep=lambda _s: None,
    )
    assert rc == 0
    assert waited == [4242]  # waited for the old pid first
    assert len(spawned) == 1
    assert spawned[0]["cmd"][1:] == ["-m", "jarvis.ui.web.launcher"]
    assert spawned[0]["kwargs"]["cwd"] == r"C:\repo"


def test_main_rejects_bad_argv():
    assert relauncher.main([], _spawn=lambda *a, **k: None) == 2
    assert relauncher.main(["notanint", "cwd"], _spawn=lambda *a, **k: None) == 2


def test_main_does_not_spawn_on_bad_argv():
    spawned: list[object] = []
    relauncher.main(["only-one-arg"], _spawn=lambda *a, **k: spawned.append(1))
    assert spawned == []
