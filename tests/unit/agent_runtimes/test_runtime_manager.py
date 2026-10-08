"""Runtime status, install and update jobs, and their routes."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.agent_runtimes as runtimes
from jarvis.agent_runtimes import base, manager
from jarvis.agent_runtimes.base import RuntimeStatus
from jarvis.core.config import override_provider_secrets
from jarvis.ui.web.agent_runtime_routes import router


class FakeDriver:
    """A runtime whose readiness is a marker file its scripted installer writes.

    ``ready`` / ``installed`` describe it before any job. An install always
    writes the marker (ready afterwards); an update only when ``update_fixes``.
    """

    def __init__(
        self,
        name: str,
        *,
        exit_code: int = 0,
        ready: bool = True,
        installed: bool = True,
        update_fixes: bool = True,
        marker: Path | None = None,
        version: str = "1.0.0",
    ) -> None:
        self.name = name
        self.label = name.title()
        self.exit_code = exit_code
        self.ready = ready
        self.installed = installed
        self.update_fixes = update_fixes
        self.marker = marker
        self.version = version
        self.stopped = 0
        self.refreshed = 0
        self.is_busy = False
        self.installs: list[str | None] = []

    def detect(self, *, refresh: bool = False) -> RuntimeStatus:
        self.refreshed += int(refresh)
        fixed = self.marker is not None and self.marker.exists()
        ready = self.ready or fixed
        installed = self.installed or fixed
        kind = "" if ready else ("outdated" if installed else "not_installed")
        return RuntimeStatus(
            self.name,
            self.label,
            installed=installed,
            version=self.version,
            ready=ready,
            problem="" if ready else "not ready",
            problem_kind=kind,
        )

    def _script(self, word: str, fixes: bool) -> list[str]:
        write = f"open({str(self.marker)!r}, 'w').close(); " if fixes and self.marker else ""
        code = f"print('{word}'); {write}raise SystemExit({self.exit_code})"
        return [sys.executable, "-c", code]

    def install_command(self, revision: str | None = None) -> list[str]:
        self.installs.append(revision)
        return self._script(f"installing {revision or 'tested'}", True)

    def update_command(self) -> list[str]:
        return self._script("updating", self.update_fixes)

    def revision(self, status: RuntimeStatus) -> str:
        return status.version

    def busy(self) -> bool:
        return self.is_busy

    async def stop(self, agent_id: str | None = None) -> None:
        self.stopped += 1


@pytest.fixture(autouse=True)
def _no_persistent_path(monkeypatch: pytest.MonkeyPatch):
    """Setup jobs never read this machine's registry PATH in tests."""
    monkeypatch.setattr(manager, "_refresh_path", lambda: None)


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    drivers = {"hermes": FakeDriver("hermes"), "openclaw": FakeDriver("openclaw", exit_code=3)}
    monkeypatch.setattr(runtimes, "_DRIVERS", drivers)
    monkeypatch.setattr(manager, "_JOBS", {})
    monkeypatch.setattr(manager, "_TASKS", {})
    return drivers


@pytest.fixture
def unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Runtimes that are not set up yet: Hermes missing, OpenClaw outdated."""
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    drivers = {
        "hermes": FakeDriver("hermes", ready=False, installed=False, marker=tmp_path / "h.ok"),
        "openclaw": FakeDriver(
            "openclaw", ready=False, update_fixes=False, marker=tmp_path / "o.ok"
        ),
    }
    monkeypatch.setattr(runtimes, "_DRIVERS", drivers)
    monkeypatch.setattr(manager, "_JOBS", {})
    monkeypatch.setattr(manager, "_TASKS", {})
    return drivers


async def _finish(name: str) -> manager.RuntimeJob:
    for _ in range(200):
        current = manager.job(name)
        assert current is not None
        if current.state != "running":
            return current
        await asyncio.sleep(0.05)
    raise AssertionError("job did not finish")


async def test_an_install_runs_the_official_command_and_logs_it(fakes):
    started = await manager.start("hermes", "install")
    assert started.state == "running"
    assert await manager.start("hermes", "install") is started  # one job at a time
    done = await _finish("hermes")
    assert done.state == "done" and done.exit_code == 0
    assert any("installing" in line for line in done.to_dict()["log_tail"])
    assert fakes["hermes"].refreshed >= 1


async def test_an_update_stops_the_runtime_first_and_reports_a_failure(fakes):
    await manager.start("openclaw", "update")
    done = await _finish("openclaw")
    assert fakes["openclaw"].stopped == 1
    assert done.state == "failed" and done.exit_code == 3
    assert "failed" in done.message


async def test_unknown_runtimes_are_refused(fakes):
    with pytest.raises(KeyError):
        await manager.start("skynet", "install")


def test_routes_list_status_and_start_jobs(fakes, monkeypatch):
    import jarvis.agent_runtimes.model_map as model_map
    import jarvis.ui.web.agent_runtime_routes as routes

    monkeypatch.setattr(model_map, "claude_login_token", lambda: None)
    routes._USABLE_CACHE[:] = [float("-inf"), [], []]
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(brain=SimpleNamespace(providers={}))
    client = TestClient(app)
    with override_provider_secrets(
        {"openai": "sk-" + "x", "claude-api": None, "openrouter": None, "grok": None,
         "nvidia": None, "gemini": None}
    ):
        body = client.get("/api/agent-runtimes").json()
    # Usable now: a saved key or a local server; every supported one otherwise.
    assert "openai" in body["supported_providers"]
    assert "claude-api" not in body["supported_providers"]
    assert "claude-api" in body["all_providers"]
    assert "openai-codex" in body["all_providers"]
    assert body["subscription_providers"] == ["openai-codex"]
    assert body["login_providers"] == []
    rows = body["runtimes"]
    assert [row["runtime"] for row in rows] == ["hermes", "openclaw"]
    assert rows[0]["ready"] is True and rows[0]["job"] is None
    assert client.post("/api/agent-runtimes/skynet/install").status_code == 404
    assert client.post("/api/agent-runtimes/hermes/delete").status_code == 422
    job = client.post("/api/agent-runtimes/hermes/install").json()["job"]
    assert job["kind"] == "install" and job["runtime"] == "hermes"
    assert client.get("/api/agent-runtimes/hermes/job").json()["job"]["kind"] == "install"


async def test_ensure_leaves_a_ready_runtime_alone(fakes):
    assert await manager.ensure("hermes") is None
    assert manager.job("hermes") is None


async def test_ensure_installs_a_missing_runtime_and_a_turn_waits_for_it(unset):
    started = await manager.ensure("hermes")
    assert started is not None and started.kind == "install"
    assert await manager.ensure("hermes") is started  # a second pick joins the job
    status = await manager.wait_ready("hermes", timeout_s=30)
    assert status.ready
    assert manager.job("hermes").state == "done"


async def test_an_update_that_leaves_the_runtime_broken_is_repaired_by_an_install(unset):
    started = await manager.ensure("openclaw")
    assert started is not None and started.kind == "update"
    status = await manager.wait_ready("openclaw", timeout_s=30)
    assert status.ready
    repair = manager.job("openclaw")
    assert repair.kind == "install" and repair.state == "done"
    assert unset["openclaw"].stopped == 1


async def test_the_daily_update_waits_while_a_turn_runs(fakes):
    fakes["hermes"].version = "0.0.1"  # older than the tested release
    fakes["hermes"].is_busy = True
    await manager._update_when_idle("hermes")
    assert manager.job("hermes") is None
    fakes["hermes"].is_busy = False
    await manager._update_when_idle("hermes")
    assert manager.job("hermes").kind == "update"
    assert manager.job("hermes").state == "done"


async def test_the_daily_update_never_moves_past_the_tested_release(fakes):
    from jarvis.agent_runtimes import versions

    tested = versions.pin("hermes").tested_text
    for installed in (tested, "999.0.0"):
        fakes["hermes"].version = installed
        await manager._update_when_idle("hermes")
        assert manager.job("hermes") is None, installed


async def test_the_daily_round_updates_only_runtimes_an_agent_uses(fakes, monkeypatch):
    monkeypatch.setattr(manager, "_FIRST_UPDATE_DELAY_S", 0)
    fakes["hermes"].version = "0.0.1"
    fakes["openclaw"].version = "0.0.1"
    rounds: list[int] = []

    async def in_use() -> set[str]:
        rounds.append(1)
        return {"hermes"}

    task = asyncio.create_task(manager.keep_current(in_use))
    try:
        for _ in range(200):
            current = manager.job("hermes")
            if current is not None and current.state != "running":
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert rounds == [1]
    assert manager.job("hermes").kind == "update"
    assert manager.job("openclaw") is None


def test_the_ensure_route_starts_what_is_needed(unset):
    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).post("/api/agent-runtimes/hermes/ensure").json()
    assert body["job"]["kind"] == "install"


def test_a_claude_login_in_the_api_key_slot_is_not_an_api_key(fakes, monkeypatch):
    import jarvis.agent_runtimes.model_map as model_map
    import jarvis.ui.web.agent_runtime_routes as routes
    from jarvis.agent_runtimes.model_map import RouteUnavailable, route_for

    config = SimpleNamespace(brain=SimpleNamespace(providers={}))
    app = FastAPI()
    app.include_router(router)
    app.state.config = config
    login = "sk-ant-" + "oat01-" + "x" * 20
    secrets = {"openai": None, "claude-api": login, "openrouter": None, "grok": None,
               "nvidia": None, "gemini": None}
    # No live Claude Code login: Claude is not offered.
    monkeypatch.setattr(model_map, "claude_login_token", lambda: None)
    routes._USABLE_CACHE[:] = [float("-inf"), [], []]
    with override_provider_secrets(secrets):
        body = TestClient(app).get("/api/agent-runtimes").json()
        with pytest.raises(RouteUnavailable, match="Claude Code login"):
            route_for(config, "claude-api", "claude-sonnet-5-5")
    assert "claude-api" not in body["supported_providers"]
    assert body["login_providers"] == []
    # A live login: Claude is offered, marked as running on the login.
    import jarvis.agent_runtimes.provider_errors as provider_errors

    monkeypatch.setattr(model_map, "claude_login_token", lambda: login)
    monkeypatch.setattr(provider_errors, "_REPORTS", {})
    monkeypatch.setattr(provider_errors, "_claude_usage", lambda token: {})
    routes._USABLE_CACHE[:] = [float("-inf"), [], []]
    with override_provider_secrets(secrets):
        body = TestClient(app).get("/api/agent-runtimes").json()
    assert "claude-api" in body["supported_providers"]
    assert body["login_providers"] == ["claude-api"]
    assert body["access_blocked"] == {}
    # Extra Usage off: the way exists, the dialog shows it disabled with why.
    monkeypatch.setattr(provider_errors, "_REPORTS", {})
    monkeypatch.setattr(
        provider_errors, "_claude_usage", lambda token: {"extra_usage": {"is_enabled": False}}
    )
    routes._USABLE_CACHE[:] = [float("-inf"), [], []]
    with override_provider_secrets(secrets):
        body = TestClient(app).get("/api/agent-runtimes").json()
    assert body["access_blocked"] == {"claude-api": {"subscription": "extra_usage_off"}}


# ------------------------------------------------- gate, prepare, rollback


class GatedDriver(FakeDriver):
    """A fake driver with the real shared/exclusive runtime gate."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.gate = base.RuntimeGate()
        self.prepared: list[str] = []
        self.prepare_needed = False

    def needs_prepare(self, status: RuntimeStatus) -> bool:
        return self.prepare_needed and not self.prepared

    def prepare_command(self) -> tuple[list[str], dict[str, str]]:
        return [sys.executable, "-c", "print('preparing')"], dict(os.environ)

    def mark_prepared(self, status: RuntimeStatus) -> None:
        self.prepared.append(status.version)


async def test_an_update_waits_for_a_running_turn_and_holds_new_turns(fakes, monkeypatch):
    gated = GatedDriver("openclaw")
    monkeypatch.setitem(runtimes._DRIVERS, "openclaw", gated)
    await gated.gate.acquire_turn(1)  # a turn is running
    started = await manager.start("openclaw", "update")
    await asyncio.sleep(0.3)
    assert started.state == "running" and gated.stopped == 0  # nothing touched yet
    # A new turn arriving now waits for the update, not the other way round.
    with pytest.raises(TimeoutError):
        await gated.gate.acquire_turn(0.2)
    gated.gate.release_turn()
    done = await _finish("openclaw")
    assert done.state == "done" and gated.stopped == 1
    await gated.gate.acquire_turn(1)  # turns run again afterwards
    gated.gate.release_turn()


async def test_a_ready_runtime_that_needs_preparing_gets_a_prepare_job(fakes, monkeypatch):
    gated = GatedDriver("hermes")
    gated.prepare_needed = True
    monkeypatch.setitem(runtimes._DRIVERS, "hermes", gated)
    started = await manager.ensure("hermes")
    assert started is not None and started.kind == "prepare"
    status = await manager.wait_ready("hermes", timeout_s=30)
    assert status.ready and gated.prepared == ["1.0.0"]
    assert await manager.ensure("hermes") is None  # prepared once per build


async def test_a_failed_setup_rolls_back_to_the_last_good_release(unset, monkeypatch):
    from jarvis.agent_runtimes import versions

    broken = FakeDriver("openclaw", ready=False, update_fixes=False, exit_code=1)
    monkeypatch.setitem(runtimes._DRIVERS, "openclaw", broken)
    versions.remember_good("openclaw", "2026.1.1", "2026.1.1")
    await manager.start("openclaw", "update")
    await manager.wait_ready("openclaw", timeout_s=30)
    # Update failed -> fresh install of the tested release -> back to last good.
    assert broken.installs == [None, "2026.1.1"]


async def test_a_successful_setup_is_remembered_for_rollback(unset):
    from jarvis.agent_runtimes import versions

    await manager.ensure("hermes")
    await manager.wait_ready("hermes", timeout_s=30)
    assert versions.last_good("hermes") == {"version": "1.0.0", "revision": "1.0.0"}


async def test_a_missing_tool_is_the_reason_the_chat_shows(fakes, monkeypatch):
    code = (
        "import sys; print('Setup needs git. Install it with your system package manager, "
        f"then try again.', file=sys.stderr); raise SystemExit({base.SETUP_MISSING_TOOL_EXIT})"
    )
    monkeypatch.setattr(
        fakes["hermes"], "install_command", lambda revision=None: [sys.executable, "-c", code]
    )
    await manager.start("hermes", "install")
    done = await _finish("hermes")
    assert done.state == "failed"
    assert manager.failure_reason("hermes").startswith("Setup needs git.")


async def test_a_failed_job_names_the_installers_last_words(fakes, monkeypatch):
    code = "print('download refused: HTTP 404'); raise SystemExit(22)"
    monkeypatch.setattr(
        fakes["hermes"], "install_command", lambda revision=None: [sys.executable, "-c", code]
    )
    await manager.start("hermes", "install")
    done = await _finish("hermes")
    assert "exit 22" in done.message and "HTTP 404" in done.message


async def test_a_missing_shell_fails_plainly(fakes, monkeypatch):
    monkeypatch.setattr(
        fakes["hermes"], "install_command", lambda revision=None: ["bash", "-c", "true"]
    )
    monkeypatch.setattr(manager.shutil, "which", lambda name: None)
    await manager.start("hermes", "install")
    done = await _finish("hermes")
    assert done.state == "failed" and "Setup needs bash" in done.message


# ------------------------------------------------------ installer wrappers


def _bash() -> str | None:
    import shutil

    found = shutil.which("bash")
    if sys.platform == "win32" and (not found or "system32" in found.lower()):
        # WSL's launcher is not a POSIX shell for this process; Git for
        # Windows' bash is.
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return found


@pytest.mark.skipif(_bash() is None, reason="no bash on this machine")
def test_the_posix_installer_reports_a_missing_tool():
    import subprocess

    argv = base.posix_installer(
        "https://example.invalid/install.sh", ["--x"], requires=("jarvis-no-such-tool",)
    )
    done = subprocess.run([_bash(), *argv[1:]], capture_output=True, text=True, timeout=60)
    assert done.returncode == base.SETUP_MISSING_TOOL_EXIT
    # The first missing tool is named: curl itself on a bare container.
    import shutil

    first = "curl" if shutil.which("curl") is None else "jarvis-no-such-tool"
    assert f"Setup needs {first}" in done.stderr


@pytest.mark.skipif(_bash() is None, reason="no bash on this machine")
def test_the_posix_installer_fails_when_the_download_fails():
    """``curl | bash`` reported success here (bash ran an empty script)."""
    import shutil
    import subprocess

    if shutil.which("curl") is None:
        pytest.skip("no curl on this machine")
    argv = base.posix_installer("https://127.0.0.1:9/install.sh", ["--x"])
    done = subprocess.run([_bash(), *argv[1:]], capture_output=True, text=True, timeout=60)
    assert done.returncode != 0


def test_installers_are_pinned_and_skip_the_browser_and_desktop_control(monkeypatch):
    from jarvis.agent_runtimes import hermes, openclaw, versions

    for windows in (True, False):
        monkeypatch.setattr(hermes, "is_windows", lambda w=windows: w)
        monkeypatch.setattr(openclaw, "is_windows", lambda w=windows: w)
        text = " ".join(hermes.HermesRuntime().install_command() or [])
        assert versions.pin("hermes").commit in text
        assert ("-SkipBrowser" in text) if windows else ("--skip-browser" in text)
        assert "computer" in text.lower()
        claw = " ".join(openclaw.OpenClawRuntime().install_command() or [])
        assert versions.pin("openclaw").tested_text in claw
        if windows:
            # Private Node only: never the full installer that tries winget (UAC).
            assert "-NodeOnly" in claw and "winget" not in claw
        else:
            assert "install-cli.sh" in claw and "--prefix" in claw
        # Updates go to the same pinned release, never to upstream latest.
        assert "update" not in (hermes.HermesRuntime().update_command() or [])


def test_the_windows_openclaw_install_runs_npm_scripts_on_the_private_node(monkeypatch):
    """npm runs OpenClaw's preinstall via ``cmd /c node``; CI's system Node 22
    was picked and rejected (2026-10-07). The private Node must come first."""
    from jarvis.agent_runtimes import openclaw

    monkeypatch.setattr(openclaw, "is_windows", lambda: True)
    text = " ".join(openclaw.OpenClawRuntime().install_command() or [])
    node_dir = str(openclaw.cli_prefix() / "node")
    path_set = text.index("$env:Path = '" + node_dir + ";'")
    assert path_set < text.index("npm-cli.js")
