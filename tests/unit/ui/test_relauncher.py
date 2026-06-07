"""Unit tests for the detached self-restart helper (jarvis.ui.relauncher).

The wait loop, command construction, argv handling, the new-instance
verify/retry loop, and the dying-app quit sequence are all pure and injectable,
so they are tested without spawning real processes or actually exiting.
"""
from __future__ import annotations

from types import SimpleNamespace

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
        return SimpleNamespace(pid=999)

    waited: list[int] = []
    rc = relauncher.main(
        ["4242", r"C:\repo"],
        _wait=lambda pid, **_kw: waited.append(pid) or True,
        _spawn=fake_spawn,
        _sleep=lambda _s: None,
        _alive=lambda _p: True,  # old pid 'alive' → one wait; new pid stays up
    )
    assert rc == 0
    assert waited == [4242]  # waited for the old pid first
    assert len(spawned) == 1
    assert spawned[0]["cmd"][1:] == ["-m", "jarvis.ui.web.launcher"]
    assert spawned[0]["kwargs"]["cwd"] == r"C:\repo"


def test_main_retries_when_new_instance_bounces():
    """A new instance that bounces off the held lock (dies fast) → retry."""
    spawned: list[list[str]] = []
    pids = iter([901, 902])

    def fake_spawn(cmd, **kwargs):
        spawned.append(cmd)
        return SimpleNamespace(pid=next(pids))

    # Old pid already gone; first new instance (901) bounces, second (902) holds.
    alive = {4242: False, 901: False, 902: True}
    rc = relauncher.main(
        ["4242", "cwd"],
        _wait=lambda pid, **_kw: True,
        _spawn=fake_spawn,
        _sleep=lambda _s: None,
        _alive=lambda p: alive.get(p, True),
    )
    assert rc == 0
    assert len(spawned) == 2  # bounced once, succeeded on retry


def test_main_gives_up_after_three_failed_spawns():
    """Every spawned instance dies → exhaust retries and report failure (1)."""
    spawned: list[list[str]] = []

    def fake_spawn(cmd, **kwargs):
        spawned.append(cmd)
        return SimpleNamespace(pid=900 + len(spawned))

    rc = relauncher.main(
        ["4242", "cwd"],
        _wait=lambda pid, **_kw: True,
        _spawn=fake_spawn,
        _sleep=lambda _s: None,
        _alive=lambda _p: False,  # nothing ever stays up
    )
    assert rc == 1
    assert len(spawned) == 3


def test_main_rejects_bad_argv():
    assert relauncher.main([], _spawn=lambda *a, **k: None) == 2
    assert relauncher.main(["notanint", "cwd"], _spawn=lambda *a, **k: None) == 2


def test_main_does_not_spawn_on_bad_argv():
    spawned: list[object] = []
    relauncher.main(["only-one-arg"], _spawn=lambda *a, **k: spawned.append(1))
    assert spawned == []


def test_new_instance_settled_true_when_alive_throughout():
    assert relauncher._new_instance_settled(
        555, _alive=lambda _p: True, _sleep=lambda _s: None, checks=3
    )


def test_new_instance_settled_false_when_it_dies():
    assert not relauncher._new_instance_settled(
        555, _alive=lambda _p: False, _sleep=lambda _s: None, checks=3
    )


def test_restart_quit_sequence_marks_quit_destroys_then_hard_exits():
    order: list[object] = []
    relauncher.run_restart_quit_sequence(
        set_quit=lambda: order.append("quit"),
        destroy_window=lambda: order.append("destroy"),
        _sleep=lambda _s: order.append(("sleep", _s)),
        _exit=lambda code: order.append(("exit", code)),
    )
    assert ("exit", 0) in order
    assert order.index("quit") < order.index("destroy") < order.index(("exit", 0))


def test_restart_quit_sequence_hard_exits_even_if_destroy_raises():
    exited: list[int] = []

    def boom():
        raise RuntimeError("destroy failed")

    relauncher.run_restart_quit_sequence(
        set_quit=lambda: None,
        destroy_window=boom,
        _sleep=lambda _s: None,
        _exit=lambda code: exited.append(code),
    )
    assert exited == [0]
