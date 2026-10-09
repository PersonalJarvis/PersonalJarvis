"""Runtime status, install and update jobs, and their routes."""

from __future__ import annotations

import asyncio
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
    ) -> None:
        self.name = name
        self.label = name.title()
        self.exit_code = exit_code
        self.ready = ready
        self.installed = installed
        self.update_fixes = update_fixes
        self.marker = marker
        self.stopped = 0
        self.refreshed = 0
        self.is_busy = False

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
            version="1.0.0",
            ready=ready,
            problem="" if ready else "not ready",
            problem_kind=kind,
        )

    def _script(self, word: str, fixes: bool) -> list[str]:
        write = f"open({str(self.marker)!r}, 'w').close(); " if fixes and self.marker else ""
        code = f"print('{word}'); {write}raise SystemExit({self.exit_code})"
        return [sys.executable, "-c", code]

    def install_command(self) -> list[str]:
        return self._script("installing", True)

    def update_command(self) -> list[str]:
        return self._script("updating", self.update_fixes)

    def busy(self) -> bool:
        return self.is_busy

    async def stop(self, agent_id: str | None = None) -> None:
        self.stopped += 1


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    monkeypatch.setattr(manager, "runtimes_root", lambda: tmp_path)
    drivers = {"hermes": FakeDriver("hermes"), "openclaw": FakeDriver("openclaw", exit_code=3)}
    monkeypatch.setattr(runtimes, "_DRIVERS", drivers)
    monkeypatch.setattr(manager, "_JOBS", {})
    monkeypatch.setattr(manager, "_TASKS", {})
    return drivers


@pytest.fixture
def unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Runtimes that are not set up yet: Hermes missing, OpenClaw outdated."""
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    monkeypatch.setattr(manager, "runtimes_root", lambda: tmp_path)
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
    fakes["hermes"].is_busy = True
    await manager._update_when_idle("hermes")
    assert manager.job("hermes") is None
    fakes["hermes"].is_busy = False
    await manager._update_when_idle("hermes")
    assert manager.job("hermes").kind == "update"
    assert manager.job("hermes").state == "done"


async def test_the_daily_round_updates_only_runtimes_an_agent_uses(fakes, monkeypatch):
    monkeypatch.setattr(manager, "_FIRST_UPDATE_DELAY_S", 0)
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
    monkeypatch.setattr(model_map, "claude_login_token", lambda: login)
    routes._USABLE_CACHE[:] = [float("-inf"), [], []]
    with override_provider_secrets(secrets):
        body = TestClient(app).get("/api/agent-runtimes").json()
    assert "claude-api" in body["supported_providers"]
    assert body["login_providers"] == ["claude-api"]
