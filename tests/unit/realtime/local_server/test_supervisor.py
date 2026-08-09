"""The lifecycle owner: one spawn path, pidfile ownership, honest refusals.

What these tests pin is the safety story of supervising a process the app
does not host: a spawn happens only when it can help (no port squatter, no
mid-install venv, no crash-loop hammering), a stop only ever kills the
process the pidfile PROVABLY owns (PID-reuse safe), and the Ollama brain
warm-up parses its endpoint from the launch command instead of asking any
provider registry (AP-21: the artifact's own capability).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.realtime.local_server import supervisor


def _spawn_ready(monkeypatch, tmp_path: Path) -> list[dict[str, Any]]:
    """Common arrangement: closed port, no pidfile, fake Popen, tmp data dir.

    Only launch commands starting with "serve" are recorded: the hardened
    child env's keyring lookup can shell out on its own (a `ver` subprocess
    on Windows), and counting those as server spawns failed the rate-limit
    assertion for the wrong reason.
    """
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
    spawned: list[dict[str, Any]] = []

    def fake_popen(command: Any, **kwargs: Any) -> SimpleNamespace:
        head = command if isinstance(command, str) else " ".join(command)
        if head.startswith("serve"):
            spawned.append({"command": command, **kwargs})
        return SimpleNamespace(pid=4711)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return spawned


# ── Address parsing ──────────────────────────────────────────────────────
def test_host_port_parses_the_configured_address() -> None:
    assert supervisor._host_port("http://localhost:8765") == ("localhost", 8765)
    assert supervisor._host_port("http://127.0.0.1:9000/v1") == ("127.0.0.1", 9000)
    assert supervisor._host_port("") == ("127.0.0.1", 8765)
    assert supervisor._host_port("localhost") == ("localhost", 8765)
    assert supervisor._host_port("http://[::1]:9000/v1") == ("::1", 9000)
    assert supervisor._host_port("http://gpu.lan:8443") == ("gpu.lan", 8443)


def test_pool_url_normalizes_every_supported_input_shape() -> None:
    assert supervisor._pool_url("http://localhost:8765") == ("http://127.0.0.1:8765/v1/pool")
    assert supervisor._pool_url("ws://localhost:8765/v1/realtime") == (
        "http://127.0.0.1:8765/v1/pool"
    )
    assert supervisor._pool_url("http://[::1]:8765/v1") == ("http://[::1]:8765/v1/pool")


def test_runtime_probe_requires_a_valid_loaded_pool(monkeypatch) -> None:
    import http.client

    class _Response:
        status = 200

        def __init__(self, payload: object) -> None:
            self._payload = json.dumps(payload).encode("utf-8")

        def read(self, limit: int) -> bytes:
            return self._payload[:limit]

    payload = {
        "size": 2,
        "in_use": 1,
        "units": [
            {"index": 0, "state": "active", "session_id": "private"},
            {"index": 1, "state": "idle", "session_id": None},
        ],
    }
    class _Connection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> None:
            pass

        def getresponse(self) -> _Response:
            return _Response(payload)

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", _Connection)
    assert supervisor.probe_runtime("http://localhost:8765") == {
        "size": 2,
        "in_use": 1,
        "available": 1,
        "stuck": 0,
    }

    payload["units"] = []
    assert supervisor.probe_runtime("http://localhost:8765") is None


def test_wait_until_ready_ignores_tcp_and_waits_for_the_pool(monkeypatch) -> None:
    answers = iter([None, None, {"size": 1, "in_use": 0, "available": 1, "stuck": 0}])
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: next(answers))
    assert supervisor.wait_until_ready("http://127.0.0.1:8765", timeout=1.0, poll_interval=0.001)


def test_managed_readiness_timeout_cleans_up_the_zombie(monkeypatch, tmp_path) -> None:
    root = tmp_path / "local_realtime"
    entrypoint = root / "venv" / "server.exe"
    command = f'"{entrypoint}" --mode realtime'
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "managed_install_root", lambda value: root)
    generation = (4711, 1000.0, "generation-a")
    monkeypatch.setattr(supervisor, "_owned_generation", lambda: generation)
    cleaned: list[tuple[Path, tuple[int, float, str]]] = []
    monkeypatch.setattr(
        supervisor,
        "_cleanup_timed_out_generation",
        lambda **kwargs: cleaned.append(
            (kwargs["install_root"], kwargs["expected_generation"])
        )
        or ("completed", "stopped pid 4711"),
    )

    assert not supervisor.wait_until_ready(
        "http://127.0.0.1:8765",
        timeout=0.0,
        launch_command=command,
        cleanup_on_timeout=True,
    )
    assert cleaned == [(root, generation)]


def test_timeout_cleanup_skips_a_newer_owned_generation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    expected = (4711, 1000.0, "generation-a")
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        supervisor,
        "_owned_generation",
        lambda: (7331, 2000.0, "generation-b"),
    )

    def forbidden_stop(**kwargs):
        raise AssertionError("a stale waiter must not stop a newer child")

    monkeypatch.setattr(supervisor, "_stop_owned_unlocked", forbidden_stop)
    outcome, message = supervisor._cleanup_timed_out_generation(
        base_url="http://127.0.0.1:8765",
        install_root=tmp_path / "local_realtime",
        expected_generation=expected,
    )
    assert outcome == "skipped"
    assert "generation changed" in message


def test_timeout_cleanup_reprobes_readiness_under_the_lease(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    pool = {"size": 1, "in_use": 0, "available": 1, "stuck": 0}
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: pool)

    def forbidden_stop(**kwargs):
        raise AssertionError("a ready child must not be stopped")

    monkeypatch.setattr(supervisor, "_stop_owned_unlocked", forbidden_stop)
    outcome, _message = supervisor._cleanup_timed_out_generation(
        base_url="http://127.0.0.1:8765",
        install_root=tmp_path / "local_realtime",
        expected_generation=(4711, 1000.0, "generation-a"),
    )
    assert outcome == "ready"


# ── ensure_running: refusals ─────────────────────────────────────────────
def test_no_launch_command_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    outcome = supervisor.ensure_running(
        launch_command="", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "refused:no-launch-command"


def test_remote_targets_are_refused(tmp_path, monkeypatch) -> None:
    """Launching a process because a LAN box went down would start a second
    server on the wrong host."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://gpu.lan:8443", reason="test"
    )
    assert outcome == "refused:not-local"


def test_a_byo_listener_without_the_private_pool_is_reused(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "already-running"


def test_an_unowned_managed_nonprotocol_listener_blocks_spawn(monkeypatch, tmp_path) -> None:
    from jarvis.realtime.local_server import install

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    root = install.install_root()
    entrypoint = root / "venv" / "server.exe"
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    outcome = supervisor.ensure_running(
        launch_command=f'"{entrypoint}" --mode realtime',
        base_url="http://localhost:8765",
        reason="test",
    )
    assert outcome == "refused:port-in-use"


def test_a_ready_protocol_server_means_already_running(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        supervisor,
        "probe_runtime",
        lambda *args, **kwargs: {
            "size": 1,
            "in_use": 0,
            "available": 1,
            "stuck": 0,
        },
    )
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "already-running"


def test_an_alive_owned_process_is_not_double_spawned(monkeypatch, tmp_path) -> None:
    """Mid-boot the server exists but has not bound its port yet — a second
    spawn would fight it for the GPU and the port."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (4711, True))
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "already-running"


def test_slow_managed_cold_start_survives_beyond_interactive_budget(
    monkeypatch, tmp_path
) -> None:
    """A healthy cold boot observed at 122 s must not be mistaken for a zombie."""
    from jarvis.realtime.local_server import install

    supervisor._reset_for_tests()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    entrypoint = install.install_root() / "venv" / "server.exe"
    command = f'"{entrypoint}" --mode realtime --ws_host 127.0.0.1'
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps(
            {
                "pid": 4711,
                "create_time": 1000.0,
                "spawned_at": time.time() - 180.0,
                "port": 8765,
                "command": command,
                "spawn_token": "slow-cold-start",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)

    def forbidden_kill(pid: int) -> bool:
        raise AssertionError(f"progressing cold-start pid {pid} must stay alive")

    def forbidden_spawn(candidate: str, **kwargs: Any) -> int:
        raise AssertionError(f"must not double-spawn while {candidate!r} is loading")

    monkeypatch.setattr(supervisor, "_kill_pid_tree", forbidden_kill)
    monkeypatch.setattr(supervisor, "_spawn", forbidden_spawn)

    outcome = supervisor.ensure_running(
        launch_command=command,
        base_url="http://127.0.0.1:8765",
        reason="test",
    )

    assert supervisor.OWNED_STARTUP_TIMEOUT_S >= 300.0
    assert outcome == "already-running"


def test_an_owned_managed_zombie_is_replaced_after_startup_deadline(
    monkeypatch, tmp_path
) -> None:
    from jarvis.realtime.local_server import install

    supervisor._reset_for_tests()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    entrypoint = install.install_root() / "venv" / "server.exe"
    command = f'"{entrypoint}" --mode realtime'
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps(
            {
                "pid": 4711,
                "create_time": 1000.0,
                "spawned_at": time.time() - supervisor.OWNED_STARTUP_TIMEOUT_S - 1,
                "port": 8765,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
    killed: list[int] = []
    monkeypatch.setattr(
        supervisor, "_kill_pid_tree", lambda pid: killed.append(pid) or True
    )
    monkeypatch.setattr(supervisor, "_kill_by_install_root", lambda root: (0, 0))
    spawned: list[str] = []
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda candidate, **kwargs: spawned.append(candidate) or 7331,
    )
    monkeypatch.setattr(supervisor, "_write_pidfile", lambda *args: True)

    outcome = supervisor.ensure_running(
        launch_command=command,
        base_url="http://127.0.0.1:8765",
        reason="test",
    )

    assert outcome == "spawned"
    assert killed == [4711]
    assert spawned and "--ws_host 127.0.0.1" in spawned[0]


def test_a_running_install_blocks_the_spawn(monkeypatch, tmp_path) -> None:
    """Spawning a half-installed venv proves nothing and locks files the
    installer is about to replace."""
    from jarvis.realtime.local_server import install

    _spawn_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(
        install, "snapshot", lambda: {"running": True, "phase": "deps"}
    )
    entrypoint = install.install_root() / "venv" / "server.exe"
    outcome = supervisor.ensure_running(
        launch_command=f'"{entrypoint}" --mode realtime',
        base_url="http://localhost:8765",
        reason="test",
    )
    assert outcome == "refused:install-running"


def test_spawns_are_rate_limited(monkeypatch, tmp_path) -> None:
    """AP-24 doctrine: a crash-looping server is marked bad, not hammered."""
    spawned = _spawn_ready(monkeypatch, tmp_path)
    first = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    # Simulate the just-spawned process crashing. A healthy owner correctly
    # returns already-running; the persisted spawn timestamp is what must
    # still stop a different Jarvis process from immediately replacing it.
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (4711, False))
    second = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert first == "spawned"
    assert second == "refused:rate-limited"
    assert len(spawned) == 1


def test_a_failing_spawn_is_reported_not_raised(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)

    def broken_popen(command: Any, **kwargs: Any) -> SimpleNamespace:
        raise FileNotFoundError(command)

    monkeypatch.setattr(subprocess, "Popen", broken_popen)
    outcome = supervisor.ensure_running(
        launch_command="gone-program", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "refused:spawn-failed"


def test_a_failing_spawn_does_not_consume_the_retry_window(monkeypatch, tmp_path) -> None:
    supervisor._reset_for_tests()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    attempts = iter([None, 7331])
    monkeypatch.setattr(supervisor, "_spawn", lambda *args, **kwargs: next(attempts))
    monkeypatch.setattr(supervisor, "_write_pidfile", lambda *args: True)

    first = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    second = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )

    assert first == "refused:spawn-failed"
    assert second == "spawned"


def test_an_abandoned_lock_file_is_not_a_stale_lease(monkeypatch, tmp_path) -> None:
    """Kernel ownership, not file age/content, decides whether the lease is live."""
    spawned = _spawn_ready(monkeypatch, tmp_path)
    lock = tmp_path / "local_realtime_server.spawn.lock"
    lock.write_bytes(b"old-owner-metadata")
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://127.0.0.1:8765", reason="test"
    )
    assert outcome == "spawned"
    assert len(spawned) == 1
    assert lock.is_file()


def test_the_lifecycle_lease_serializes_real_processes(monkeypatch, tmp_path) -> None:
    """A thread lock cannot protect two Jarvis processes; the OS lease can."""
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    env = dict(os.environ)
    env["JARVIS_DATA_DIR"] = str(tmp_path)
    script = (
        "from jarvis.realtime.local_server import supervisor; "
        "guard=supervisor._exclusive_spawn_guard(); "
        "value=guard.__enter__(); print(value); guard.__exit__(None,None,None)"
    )
    with supervisor._exclusive_spawn_guard() as acquired:
        assert acquired is True
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=15,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_a_spawn_without_durable_ownership_is_torn_down(monkeypatch, tmp_path) -> None:
    _spawn_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(supervisor, "_write_pidfile", lambda *args: False)
    killed: list[int] = []
    monkeypatch.setattr(supervisor, "_kill_pid_tree", lambda pid: killed.append(pid) or True)
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://127.0.0.1:8765", reason="test"
    )
    assert outcome == "refused:ownership-failed"
    assert killed == [4711]


# ── ensure_running: the spawn itself ─────────────────────────────────────
def test_spawn_is_windowless_and_records_ownership(monkeypatch, tmp_path) -> None:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    spawned = _spawn_ready(monkeypatch, tmp_path)
    outcome = supervisor.ensure_running(
        launch_command="serve --flag", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "spawned"
    creationflags = int(spawned[0]["creationflags"])
    if os.name == "nt":
        assert creationflags & NO_WINDOW_CREATIONFLAGS  # AP-1
        assert creationflags & supervisor._WINDOWS_BREAKAWAY_FROM_JOB
    else:
        assert creationflags == 0
    record = json.loads(
        (tmp_path / "local_realtime_server.pid.json").read_text(encoding="utf-8")
    )
    assert record["pid"] == 4711
    assert record["port"] == 8765
    assert record["command"] == "serve --flag"
    assert "env" not in {k for k in record}  # never environment, never secrets
    assert list(tmp_path.glob("*.tmp")) == []
    assert (tmp_path / "local_realtime_server.spawn.lock").is_file()


def test_legacy_managed_command_is_bound_to_loopback_at_spawn(monkeypatch, tmp_path) -> None:
    from jarvis.realtime.local_server import install

    supervisor._reset_for_tests()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    entrypoint = install.install_root() / "venv" / "server.exe"
    command = f'"{entrypoint}" --mode realtime --ws_host 0.0.0.0'
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (None, False))
    monkeypatch.setattr(supervisor, "_kill_by_install_root", lambda root: (0, 0))
    spawned: list[str] = []
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda candidate, **kwargs: spawned.append(candidate) or 7331,
    )
    monkeypatch.setattr(supervisor, "_write_pidfile", lambda *args: True)

    assert (
        supervisor.ensure_running(
            launch_command=command,
            base_url="http://127.0.0.1:8765",
            reason="test",
        )
        == "spawned"
    )
    assert spawned[0].count("--ws_host") == 1
    assert "--ws_host 127.0.0.1" in spawned[0]
    assert "0.0.0.0" not in spawned[0]  # noqa: S104 - legacy input assertion


def test_a_ready_owned_legacy_bind_is_migrated_to_loopback(monkeypatch, tmp_path) -> None:
    from jarvis.realtime.local_server import install

    supervisor._reset_for_tests()
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    entrypoint = install.install_root() / "venv" / "server.exe"
    legacy = f'"{entrypoint}" --mode realtime --ws_host 0.0.0.0'
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps(
            {
                "pid": 4711,
                "create_time": 1000.0,
                "spawned_at": 1000.0,
                "port": 8765,
                "command": legacy,
            }
        ),
        encoding="utf-8",
    )
    pool = {"size": 1, "in_use": 0, "available": 1, "stuck": 0}
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: pool)
    monkeypatch.setattr(supervisor, "_port_open", lambda *args, **kwargs: False)
    stopped: list[Path] = []
    monkeypatch.setattr(
        supervisor,
        "_stop_owned_unlocked",
        lambda **kwargs: stopped.append(kwargs["install_root"]) or (True, "stopped"),
    )
    monkeypatch.setattr(supervisor, "_kill_by_install_root", lambda root: (0, 0))
    spawned: list[str] = []
    monkeypatch.setattr(
        supervisor,
        "_spawn",
        lambda command, **kwargs: spawned.append(command) or 7331,
    )
    monkeypatch.setattr(supervisor, "_write_pidfile", lambda *args: True)

    outcome = supervisor.ensure_running(
        launch_command=legacy,
        base_url="http://127.0.0.1:8765",
        reason="upgrade",
    )

    assert outcome == "spawned"
    assert stopped == [install.install_root()]
    assert spawned[0].count("--ws_host") == 1
    assert "--ws_host 127.0.0.1" in spawned[0]


def test_a_ready_owned_loopback_bind_is_not_restarted(monkeypatch, tmp_path) -> None:
    from jarvis.realtime.local_server import install

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    entrypoint = install.install_root() / "venv" / "server.exe"
    command = f'"{entrypoint}" --mode realtime --ws_host 127.0.0.1'
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps(
            {
                "pid": 4711,
                "create_time": 1000.0,
                "port": 8765,
                "command": command,
            }
        ),
        encoding="utf-8",
    )
    pool = {"size": 1, "in_use": 0, "available": 1, "stuck": 0}
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: pool)

    def forbidden_stop(**kwargs):
        raise AssertionError("a loopback-bound server must stay alive")

    monkeypatch.setattr(supervisor, "_stop_owned_unlocked", forbidden_stop)
    assert (
        supervisor.ensure_running(
            launch_command=command,
            base_url="http://127.0.0.1:8765",
            reason="upgrade",
        )
        == "already-running"
    )


def test_pid_reuse_is_never_trusted(monkeypatch, tmp_path) -> None:
    """A rebooted machine can hand the recorded pid to an innocent process;
    a create_time mismatch must read as NOT ours."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps({"pid": 4711, "create_time": 1000.0, "port": 8765}),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 2000.0)
    pid, alive = supervisor._owned_process()
    assert pid == 4711
    assert alive is False


def test_matching_create_time_is_ownership(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps({"pid": 4711, "create_time": 1000.0, "port": 8765}),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.4)
    assert supervisor._owned_process() == (4711, True)


# ── stop ─────────────────────────────────────────────────────────────────
def test_stop_without_ownership_changes_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    changed, message = supervisor.stop(owned_only=True)
    assert changed is False
    assert "no owned" in message


def test_stop_kills_the_verified_tree_and_sweeps_managed_orphans(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    install_root = tmp_path / "local_realtime"
    install_root.mkdir()
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps({"pid": 4711, "create_time": 1000.0, "port": 8765}),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
    killed: list[int] = []
    monkeypatch.setattr(supervisor, "_kill_pid_tree", lambda pid: killed.append(pid) or True)
    swept: list[Path] = []
    monkeypatch.setattr(
        supervisor,
        "_kill_by_install_root",
        lambda root: swept.append(root) or (2, 0),
    )
    changed, message = supervisor.stop(owned_only=True, install_root=install_root)
    assert changed is True
    assert killed == [4711]
    assert swept == [install_root]
    assert "2 managed process(es)" in message
    assert not (tmp_path / "local_realtime_server.pid.json").exists()


def test_stop_succeeds_when_tree_kill_fails_but_managed_sweep_recovers(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    root = tmp_path / "local_realtime"
    root.mkdir()
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps({"pid": 4711, "create_time": 1000.0, "port": 8765}),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
    monkeypatch.setattr(supervisor, "_kill_pid_tree", lambda pid: False)
    monkeypatch.setattr(supervisor, "_kill_by_install_root", lambda value: (1, 0))

    changed, message = supervisor.stop(owned_only=True, install_root=root)

    assert changed is True
    assert "1 managed process(es)" in message
    assert not (tmp_path / "local_realtime_server.pid.json").exists()


def test_stop_fails_closed_when_a_managed_process_survives(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    root = tmp_path / "local_realtime"
    root.mkdir()
    monkeypatch.setattr(supervisor, "_kill_by_install_root", lambda value: (1, 1))

    changed, message = supervisor.stop(owned_only=True, install_root=root)

    assert changed is False
    assert "could not stop 1 managed process" in message


def test_windows_taskkill_success_code_is_not_enough_if_pid_survives(monkeypatch) -> None:
    monkeypatch.setattr(supervisor.os, "name", "nt")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b""),
    )
    monkeypatch.setattr(supervisor, "_pid_exists", lambda pid: True)
    moments = iter([0.0, 6.0])
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(moments))

    assert supervisor._kill_pid_tree(4711) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows process-object semantics")
def test_windows_pid_probe_rejects_an_exited_but_still_referenced_process() -> None:
    """A signalled process object is dead even while Python retains its handle."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    proc.wait(timeout=10)

    assert supervisor._pid_exists(proc.pid) is False


def test_managed_server_windows_flags_detach_and_break_away() -> None:
    flags = supervisor._server_creationflags(platform_name="nt")

    assert flags & supervisor._WINDOWS_NO_WINDOW
    assert flags & supervisor._WINDOWS_DETACHED_PROCESS
    assert flags & supervisor._WINDOWS_BREAKAWAY_FROM_JOB


def test_spawn_retries_without_breakaway_when_the_host_job_forbids_it(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    attempts: list[int] = []

    class _Process:
        pid = 4711

    def popen(*args, **kwargs):
        attempts.append(int(kwargs["creationflags"]))
        if len(attempts) == 1:
            raise PermissionError("breakaway denied")
        return _Process()

    flags = NO_WINDOW_CREATIONFLAGS | supervisor._WINDOWS_BREAKAWAY_FROM_JOB
    monkeypatch.setattr(supervisor, "_server_creationflags", lambda: flags)
    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)

    assert supervisor._spawn("server --mode realtime", reason="test") == 4711
    assert attempts == [flags, flags & ~supervisor._WINDOWS_BREAKAWAY_FROM_JOB]


def test_runtime_monitor_restarts_and_rewarms_a_dead_owned_server(
    monkeypatch, tmp_path
) -> None:
    from jarvis.realtime.local_server import install

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(install, "server_status", lambda: {"ready": True})
    monkeypatch.setattr(
        supervisor,
        "ensure_running",
        lambda **kwargs: calls.append(f"ensure:{kwargs['reason']}") or "spawned",
    )
    monkeypatch.setattr(
        supervisor,
        "wait_until_ready",
        lambda *args, **kwargs: calls.append("ready") or True,
    )
    monkeypatch.setattr(
        install,
        "repair_smoke_marker_from_live_runtime",
        lambda base_url: calls.append("marker") or True,
    )
    monkeypatch.setattr(
        supervisor,
        "warm_brain",
        lambda **kwargs: calls.append("brain") or True,
    )

    outcome = supervisor._revive_from_monitor(
        launch_command="serve --model_name m",
        base_url="http://127.0.0.1:8765",
        reason="watchdog-exit",
        cancel_event=threading.Event(),
    )

    assert outcome == "ready"
    assert calls == ["ensure:watchdog-exit", "ready", "marker", "brain"]


def test_runtime_monitor_is_singleton_for_one_managed_generation(
    monkeypatch, tmp_path
) -> None:
    from jarvis.realtime.local_server import install

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    root = tmp_path / "local_realtime"
    root.mkdir()
    command = f'"{root / "server.exe"}" --mode realtime'
    monkeypatch.setattr(install, "server_status", lambda: {"ready": True})
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (4711, True))
    started: list[tuple[object, tuple[object, ...]]] = []

    class _Thread:
        def __init__(self, *, target, args, **kwargs):
            del kwargs
            self._target = target
            self._args = args
            self._alive = False

        def start(self) -> None:
            self._alive = True
            started.append((self._target, self._args))

        def is_alive(self) -> bool:
            return self._alive

    monkeypatch.setattr(supervisor.threading, "Thread", _Thread)

    assert supervisor.start_runtime_monitor(
        launch_command=command,
        base_url="http://127.0.0.1:8765",
    )
    assert not supervisor.start_runtime_monitor(
        launch_command=command,
        base_url="http://127.0.0.1:8765",
    )
    assert len(started) == 1


def test_runtime_monitor_never_revives_an_unproven_failed_install(monkeypatch) -> None:
    from jarvis.realtime.local_server import install

    monkeypatch.setattr(install, "server_status", lambda: {"ready": False})

    def forbidden(**kwargs):
        raise AssertionError("an invalidated install must not be spawned")

    monkeypatch.setattr(supervisor, "ensure_running", forbidden)

    assert (
        supervisor._revive_from_monitor(
            launch_command="serve --model_name m",
            base_url="http://127.0.0.1:8765",
            reason="watchdog-exit",
            cancel_event=threading.Event(),
        )
        == "refused:install-unproven"
    )


def test_deliberate_stop_disarms_the_runtime_monitor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    supervisor._reset_for_tests()
    monitor_stop = threading.Event()
    monkeypatch.setattr(supervisor, "_monitor_stop", monitor_stop)
    monkeypatch.setattr(
        supervisor,
        "_stop_owned_unlocked",
        lambda **kwargs: (True, "stopped pid 4711"),
    )

    changed, _message = supervisor.stop(owned_only=True)

    assert changed is True
    assert monitor_stop.is_set()


def test_stop_clears_a_stale_pidfile(monkeypatch, tmp_path) -> None:
    """A pidfile whose process is gone is bookkeeping debt, not a target."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps({"pid": 4711, "create_time": 1000.0, "port": 8765}),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: None)
    changed, _message = supervisor.stop(owned_only=True)
    assert changed is False
    assert not (tmp_path / "local_realtime_server.pid.json").exists()


def test_install_root_matching_has_a_real_path_boundary(tmp_path) -> None:
    root = (tmp_path / "local_realtime").resolve()
    root.mkdir()
    assert supervisor._path_is_within_root(str(root / "venv" / "server.exe"), root)
    assert not supervisor._path_is_within_root(
        str(tmp_path / "local_realtime_backup" / "server.exe"), root
    )


def test_managed_sweep_ignores_an_editor_opening_a_file_in_the_tree(tmp_path) -> None:
    root = (tmp_path / "local_realtime").resolve()
    root.mkdir()
    editor = str((tmp_path / "editor.exe").resolve())
    log_file = str((root / "smoke_boot.log").resolve())
    assert not supervisor._process_identity_is_managed(
        editor,
        (editor, log_file),
        root,
    )
    managed = str((root / "venv" / "server.exe").resolve())
    assert supervisor._process_identity_is_managed(managed, (managed,), root)


def test_managed_identity_keeps_a_posix_venv_python_symlink_lexical(tmp_path) -> None:
    if os.name == "nt":
        return
    root = (tmp_path / "local_realtime").resolve()
    interpreter = root / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(Path(sys.executable))
    assert not interpreter.resolve().is_relative_to(root)
    assert supervisor._process_identity_is_managed(
        str(interpreter.resolve()),
        (str(interpreter), str(root / "venv" / "bin" / "speech-to-speech")),
        root,
    )


# ── status ───────────────────────────────────────────────────────────────
def test_status_reports_reachable_and_ownership(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    pool = {"size": 1, "in_use": 0, "available": 1, "stuck": 0}
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: pool)
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (None, False))
    status = supervisor.status("http://localhost:8765")
    assert status == {
        "reachable": True,
        "ready": True,
        "available": True,
        "pool": pool,
        "port": 8765,
        "pid": None,
        "owned": False,
        "stale": False,
    }


def test_status_never_calls_a_tcp_listener_model_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (4711, True))
    runtime = supervisor.status("http://127.0.0.1:8765")
    assert runtime["reachable"] is True
    assert runtime["ready"] is False
    assert runtime["available"] is False
    assert runtime["pool"] is None


# ── brain warm-up ────────────────────────────────────────────────────────
_COMMAND = (
    '"C:\\tree\\venv\\Scripts\\speech-to-speech.exe" --mode realtime '
    "--model_name qwen2.5:7b "
    "--responses_api_base_url http://127.0.0.1:11434/v1 "
    "--responses_api_api_key ollama"
)


def test_brain_endpoint_is_parsed_from_the_command() -> None:
    model, base = supervisor._brain_endpoint(_COMMAND)
    assert model == "qwen2.5:7b"
    assert base == "http://127.0.0.1:11434/v1"


def test_warm_brain_pings_ollama_with_keep_alive(monkeypatch) -> None:
    import urllib.request

    requests: list[tuple[str, dict[str, Any]]] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _Response:
        requests.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert supervisor.warm_brain(launch_command=_COMMAND) is True
    url, payload = requests[0]
    assert url == "http://127.0.0.1:11434/api/generate"
    assert payload["model"] == "qwen2.5:7b"
    assert payload["keep_alive"] == supervisor.BRAIN_KEEP_ALIVE


def test_warm_brain_without_a_brain_flag_is_a_noop() -> None:
    assert supervisor.warm_brain(launch_command="serve --mode realtime") is False


def test_warm_brain_swallows_a_dead_endpoint(monkeypatch) -> None:
    import urllib.error
    import urllib.request

    def dead(request: Any, timeout: float = 0.0) -> None:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", dead)
    assert supervisor.warm_brain(launch_command=_COMMAND) is False


# ── environment hardening ────────────────────────────────────────────────
def test_hf_symlink_workaround_is_windows_only(monkeypatch) -> None:
    """The WinError 1314 workaround costs gigabytes of duplicated cache on
    macOS/Linux where symlinks simply work."""
    import os as os_module

    monkeypatch.delenv("HF_HUB_DISABLE_SYMLINKS", raising=False)
    env = supervisor.hardened_child_env(inject_openai_key=False)
    if os_module.name == "nt":
        assert env.get("HF_HUB_DISABLE_SYMLINKS") == "1"
    else:
        assert "HF_HUB_DISABLE_SYMLINKS" not in env
    assert env.get("PYTHONFAULTHANDLER") == "1"
    assert env.get("PYTHONUNBUFFERED") == "1"
