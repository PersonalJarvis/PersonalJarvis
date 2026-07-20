"""Unit tests for the detached self-restart helper (jarvis.ui.relauncher).

The wait loop, command construction, argv handling, the new-instance
verify/retry loop, and the dying-app quit sequence are all pure and injectable,
so they are tested without spawning real processes or actually exiting.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from jarvis.ui import relauncher


def test_build_launch_command(monkeypatch):
    monkeypatch.setattr(relauncher.sys, "platform", "linux")
    assert relauncher.build_launch_command("py.exe") == [
        "py.exe",
        "-m",
        "jarvis.ui.web.launcher",
    ]


def test_build_launch_command_uses_macos_bundle(monkeypatch, tmp_path):
    import jarvis.setup.macos_app_bundle as bundle_module

    bundle = tmp_path / "Personal Jarvis.app"
    monkeypatch.setattr(relauncher.sys, "platform", "darwin")
    monkeypatch.setattr(bundle_module, "macos_app_bundle_path", lambda: bundle)
    monkeypatch.setattr(
        bundle_module, "macos_app_bundle_is_launchable", lambda _bundle: True
    )

    assert relauncher.build_launch_command("python3") == [
        "/usr/bin/open",
        "-W",
        "-a",
        str(bundle),
    ]


def test_build_launch_command_fails_closed_without_macos_bundle(monkeypatch, tmp_path):
    import jarvis.setup.macos_app_bundle as bundle_module

    monkeypatch.setattr(relauncher.sys, "platform", "darwin")
    monkeypatch.setattr(
        bundle_module,
        "macos_app_bundle_path",
        lambda: tmp_path / "missing.app",
    )
    assert relauncher.build_launch_command("python3") == [
        "/usr/bin/open",
        "-W",
        "-a",
        str(tmp_path / "missing.app"),
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


def test_main_waits_then_spawns_fresh_launcher(monkeypatch):
    monkeypatch.setattr(relauncher.sys, "platform", "linux")
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

    def arm_watchdog(delay, exit_fn):
        order.append(("armed", delay))
        exit_fn(0)

    relauncher.run_restart_quit_sequence(
        set_quit=lambda: order.append("quit"),
        destroy_window=lambda: order.append("destroy"),
        _sleep=lambda _s: order.append(("sleep", _s)),
        _exit=lambda code: order.append(("exit", code)),
        _arm_watchdog=arm_watchdog,
    )
    assert ("exit", 0) in order
    assert order.index("quit") < order.index(("armed", 0.7)) < order.index("destroy")


def test_restart_quit_sequence_hard_exits_even_if_destroy_raises():
    exited: list[int] = []

    def boom():
        raise RuntimeError("destroy failed")

    relauncher.run_restart_quit_sequence(
        set_quit=lambda: None,
        destroy_window=boom,
        _sleep=lambda _s: None,
        _exit=lambda code: exited.append(code),
        _arm_watchdog=lambda _delay, exit_fn: exit_fn(0),
    )
    assert exited == [0]


def test_restart_watchdog_exits_while_destroy_window_is_blocked():
    destroy_entered = threading.Event()
    release_destroy = threading.Event()
    exited = threading.Event()

    def blocked_destroy():
        destroy_entered.set()
        release_destroy.wait(timeout=1.0)

    worker = threading.Thread(
        target=relauncher.run_restart_quit_sequence,
        kwargs={
            "set_quit": lambda: None,
            "destroy_window": blocked_destroy,
            "pre_delay": 0.0,
            "hard_exit_after": 0.02,
            "_exit": lambda _code: exited.set(),
        },
        daemon=True,
    )
    worker.start()

    assert destroy_entered.wait(timeout=0.5)
    assert exited.wait(timeout=0.5)

    release_destroy.set()
    worker.join(timeout=0.5)
    assert not worker.is_alive()
