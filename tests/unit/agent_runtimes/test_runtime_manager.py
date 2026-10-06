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
    def __init__(self, name: str, *, exit_code: int = 0) -> None:
        self.name = name
        self.label = name.title()
        self.exit_code = exit_code
        self.stopped = 0
        self.refreshed = 0

    def detect(self, *, refresh: bool = False) -> RuntimeStatus:
        self.refreshed += int(refresh)
        return RuntimeStatus(self.name, self.label, installed=True, version="1.0.0", ready=True)

    def _script(self, word: str) -> list[str]:
        return [sys.executable, "-c", f"print('{word}'); raise SystemExit({self.exit_code})"]

    def install_command(self) -> list[str]:
        return self._script("installing")

    def update_command(self) -> list[str]:
        return self._script("updating")

    async def stop(self, agent_id: str | None = None) -> None:
        self.stopped += 1


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(base, "runtimes_root", lambda: tmp_path)
    drivers = {"hermes": FakeDriver("hermes"), "openclaw": FakeDriver("openclaw", exit_code=3)}
    monkeypatch.setattr(runtimes, "_DRIVERS", drivers)
    monkeypatch.setattr(manager, "_JOBS", {})
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


def test_routes_list_status_and_start_jobs(fakes):
    import jarvis.ui.web.agent_runtime_routes as routes

    routes._USABLE_CACHE[:] = [float("-inf"), []]
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
    assert "openai-codex" not in body["all_providers"]
    rows = body["runtimes"]
    assert [row["runtime"] for row in rows] == ["hermes", "openclaw"]
    assert rows[0]["ready"] is True and rows[0]["job"] is None
    assert client.post("/api/agent-runtimes/skynet/install").status_code == 404
    assert client.post("/api/agent-runtimes/hermes/delete").status_code == 422
    job = client.post("/api/agent-runtimes/hermes/install").json()["job"]
    assert job["kind"] == "install" and job["runtime"] == "hermes"
    assert client.get("/api/agent-runtimes/hermes/job").json()["job"]["kind"] == "install"
