"""OpenClaw Gateway supervision against a stand-in CLI (no real OpenClaw)."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from jarvis.agent_runtimes import base, openclaw
from jarvis.agent_runtimes.base import HomeFileLock, RuntimeTurn, RuntimeUnavailable
from jarvis.agent_runtimes.model_map import ModelRoute
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_FAKE = Path(__file__).resolve().parents[2] / "fakes" / "fake_openclaw.py"
_LAUNCHER = [sys.executable, str(_FAKE)]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path / "rt")
    folder = tmp_path / "rt" / "openclaw" / "hermit"
    folder.mkdir(parents=True)
    return folder


def _turn(tmp_path: Path) -> RuntimeTurn:
    return RuntimeTurn(
        agent_id="hermit",
        agent_name="Hermit",
        session_id="society:hermit",
        workspace=tmp_path,
        route=ModelRoute("ollama", "qwen3", "http://127.0.0.1:11434/v1", "chat_completions", None),
        resume=None,
        auto_approve=True,
    )


async def _start(runtime: openclaw.OpenClawRuntime, home: Path, port: int, env=None):
    return await runtime._start_gateway(
        _turn(home.parent), "hermit", home, port, "tok", "h", _LAUNCHER, env or {}
    )


def _env(**extra: str) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(extra)
    return env


def test_a_started_gateway_is_recorded_and_stopping_clears_it(home):
    async def run() -> None:
        runtime = openclaw.OpenClawRuntime()
        gateway = await _start(runtime, home, openclaw._free_port(), _env())
        record = json.loads((home / "gateway.pid").read_text(encoding="utf-8"))
        assert record["pid"] == gateway.proc.pid and record["port"] == gateway.port
        await runtime._stop_gateway(gateway)
        assert not (home / "gateway.pid").exists()
        # The folder's Gateway lock is free again.
        again = HomeFileLock(home / "gateway.lock")
        assert again.try_acquire()
        again.release()

    asyncio.run(run())


def test_a_gateway_left_by_a_crashed_app_is_stopped_before_a_new_one(home):
    port = openclaw._free_port()
    orphan = subprocess.Popen(  # noqa: S603 — the stand-in, started by this test
        [*_LAUNCHER, "gateway", "run", "--port", str(port)],
        cwd=home,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    try:
        created = psutil.Process(orphan.pid).create_time()
        (home / "gateway.pid").write_text(
            json.dumps({"pid": orphan.pid, "port": port, "created": created}), encoding="utf-8"
        )
        assert openclaw._reap_stale_gateway(home)
        orphan.wait(timeout=10)
        assert not (home / "gateway.pid").exists()
    finally:
        if orphan.poll() is None:
            orphan.kill()


def test_a_recycled_pid_is_never_killed(home):
    bystander = subprocess.Popen(  # noqa: S603 — started by this test
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    try:
        (home / "gateway.pid").write_text(
            json.dumps({"pid": bystander.pid, "port": 1, "created": 12345.0}), encoding="utf-8"
        )
        assert not openclaw._reap_stale_gateway(home)
        assert bystander.poll() is None
    finally:
        bystander.kill()


def test_a_second_process_never_runs_the_same_agents_gateway(home):
    holder = HomeFileLock(home / "gateway.lock")
    assert holder.try_acquire()
    try:
        with pytest.raises(RuntimeUnavailable, match="Another Jarvis process"):
            asyncio.run(_start(openclaw.OpenClawRuntime(), home, openclaw._free_port(), _env()))
    finally:
        holder.release()


def test_a_port_taken_before_the_gateway_binds_is_retried_on_a_fresh_one(home, monkeypatch):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    taken = blocker.getsockname()[1]

    async def run() -> None:
        runtime = openclaw.OpenClawRuntime()
        gateway = await _start(runtime, home, taken, _env())
        try:
            assert gateway.port != taken
            assert await openclaw._healthy(gateway.port)
            written = json.loads((home / "openclaw.json").read_text(encoding="utf-8"))
            assert written["gateway"]["port"] == gateway.port
        finally:
            await runtime._stop_gateway(gateway)

    try:
        asyncio.run(run())
    finally:
        blocker.close()


def test_a_foreign_listener_is_not_a_ready_gateway():
    """A bare TCP accept on the port proves nothing; /healthz must answer 200."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]

    async def probe() -> bool:
        return await openclaw._healthy(port)

    try:
        assert asyncio.run(probe()) is False
    finally:
        server.close()


def test_a_rejected_config_gets_one_doctor_pass(home):
    async def run() -> None:
        runtime = openclaw.OpenClawRuntime()
        gateway = await _start(
            runtime, home, openclaw._free_port(), _env(FAKE_OPENCLAW_MODE="config-once")
        )
        try:
            assert (home / "doctor.done").exists()
        finally:
            await runtime._stop_gateway(gateway)

    asyncio.run(run())


def test_a_hanging_doctor_is_stopped(home, monkeypatch):
    monkeypatch.setattr(openclaw, "_DOCTOR_TIMEOUT_S", 1.0)
    asyncio.run(
        openclaw._run_doctor(_LAUNCHER, _env(FAKE_OPENCLAW_MODE="doctor-hang"), home)
    )
    pid = int((home / "doctor.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    try:
        survivor = psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:  # gone between checks: reaped
        survivor = False
    assert not survivor


def test_jarvis_prefers_its_own_copy_of_openclaw(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    prefix = openclaw.cli_prefix()
    if openclaw.is_windows():
        (prefix / "node").mkdir(parents=True)
        (prefix / "node" / "node.exe").write_bytes(b"")
        entry = prefix / "pkg" / "node_modules" / "openclaw"
        entry.mkdir(parents=True)
        (entry / "openclaw.mjs").write_text("", encoding="utf-8")
    else:
        (prefix / "bin").mkdir(parents=True)
        wrapper = prefix / "bin" / "openclaw"
        wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
        wrapper.chmod(0o755)
    launcher = openclaw._launcher()
    assert launcher is not None and Path(launcher[0]).is_relative_to(prefix)


def test_an_install_outside_path_is_found(tmp_path, monkeypatch):
    """OpenClaw's installers use folders a GUI-launched app does not have on PATH."""
    import jarvis.core.path_augment as path_augment

    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path / "rt")
    monkeypatch.setattr(path_augment, "ensure_cli_paths", lambda: [])
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    bin_dir = tmp_path / "home" / ".openclaw" / "bin"
    bin_dir.mkdir(parents=True)
    name = "openclaw.exe" if openclaw.is_windows() else "openclaw"
    (bin_dir / name).write_bytes(b"")
    (bin_dir / name).chmod(0o755)
    monkeypatch.setattr(openclaw, "_installer_bin_dirs", lambda: (bin_dir,))
    launcher = openclaw._launcher()
    assert launcher is not None and Path(launcher[0]).parent == bin_dir


def test_moving_the_agent_folders_keeps_the_installed_openclaw(monkeypatch, tmp_path):
    """The e2e spikes keep agent state in a temp dir; they must still find the
    private OpenClaw the app installed (CI 2026-10-07: "not installed")."""
    from jarvis.agent_runtimes import base, openclaw

    installed = openclaw.cli_prefix()
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path / "agent_runtimes")
    assert openclaw.cli_prefix() == installed
