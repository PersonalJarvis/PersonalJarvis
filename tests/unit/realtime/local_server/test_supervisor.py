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
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jarvis.realtime.local_server import supervisor


def _spawn_ready(monkeypatch, tmp_path: Path) -> list[dict[str, Any]]:
    """Common arrangement: closed port, no pidfile, fake Popen, tmp data dir."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    spawned: list[dict[str, Any]] = []

    def fake_popen(command: Any, **kwargs: Any) -> SimpleNamespace:
        spawned.append({"command": command, **kwargs})
        return SimpleNamespace(pid=4711)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return spawned


# ── Address parsing ──────────────────────────────────────────────────────
def test_host_port_parses_the_configured_address() -> None:
    assert supervisor._host_port("http://localhost:8765") == ("localhost", 8765)
    assert supervisor._host_port("http://127.0.0.1:9000/v1") == ("127.0.0.1", 9000)
    assert supervisor._host_port("") == ("localhost", 8765)
    assert supervisor._host_port("localhost") == ("localhost", 8765)
    assert supervisor._host_port("http://gpu.lan:8443") == ("gpu.lan", 8443)


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


def test_a_served_port_means_already_running(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "already-running"


def test_an_alive_owned_process_is_not_double_spawned(monkeypatch, tmp_path) -> None:
    """Mid-boot the server exists but has not bound its port yet — a second
    spawn would fight it for the GPU and the port."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (4711, True))
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "already-running"


def test_a_running_install_blocks_the_spawn(monkeypatch, tmp_path) -> None:
    """Spawning a half-installed venv proves nothing and locks files the
    installer is about to replace."""
    from jarvis.realtime.local_server import install

    _spawn_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(
        install, "snapshot", lambda: {"running": True, "phase": "deps"}
    )
    outcome = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "refused:install-running"


def test_spawns_are_rate_limited(monkeypatch, tmp_path) -> None:
    """AP-24 doctrine: a crash-looping server is marked bad, not hammered."""
    spawned = _spawn_ready(monkeypatch, tmp_path)
    first = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    second = supervisor.ensure_running(
        launch_command="serve", base_url="http://localhost:8765", reason="test"
    )
    assert first == "spawned"
    assert second == "refused:rate-limited"
    assert len(spawned) == 1


def test_a_failing_spawn_is_reported_not_raised(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)

    def broken_popen(command: Any, **kwargs: Any) -> SimpleNamespace:
        raise FileNotFoundError(command)

    monkeypatch.setattr(subprocess, "Popen", broken_popen)
    outcome = supervisor.ensure_running(
        launch_command="gone-program", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "refused:spawn-failed"


# ── ensure_running: the spawn itself ─────────────────────────────────────
def test_spawn_is_windowless_and_records_ownership(monkeypatch, tmp_path) -> None:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    spawned = _spawn_ready(monkeypatch, tmp_path)
    outcome = supervisor.ensure_running(
        launch_command="serve --flag", base_url="http://localhost:8765", reason="test"
    )
    assert outcome == "spawned"
    assert spawned[0]["creationflags"] == NO_WINDOW_CREATIONFLAGS  # AP-1
    record = json.loads(
        (tmp_path / "local_realtime_server.pid.json").read_text(encoding="utf-8")
    )
    assert record["pid"] == 4711
    assert record["port"] == 8765
    assert record["command"] == "serve --flag"
    assert "env" not in {k for k in record}  # never environment, never secrets


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


def test_stop_kills_only_the_verified_pid(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    (tmp_path / "local_realtime_server.pid.json").write_text(
        json.dumps({"pid": 4711, "create_time": 1000.0, "port": 8765}),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
    killed: list[int] = []
    monkeypatch.setattr(
        supervisor, "_kill_pid_tree", lambda pid: killed.append(pid) or True
    )
    changed, message = supervisor.stop(owned_only=True)
    assert changed is True
    assert killed == [4711]
    assert not (tmp_path / "local_realtime_server.pid.json").exists()


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


# ── status ───────────────────────────────────────────────────────────────
def test_status_reports_reachable_and_ownership(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: True)
    monkeypatch.setattr(supervisor, "_owned_process", lambda: (None, False))
    status = supervisor.status("http://localhost:8765")
    assert status == {
        "reachable": True,
        "port": 8765,
        "pid": None,
        "owned": False,
        "stale": False,
    }


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
