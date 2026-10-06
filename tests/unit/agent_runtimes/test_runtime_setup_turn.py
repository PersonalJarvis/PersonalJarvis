"""A turn on a runtime that is not set up yet waits for the setup and says so."""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.agent_chat import runner_acp
from jarvis.agent_chat.runner_cli import CliUnavailable
from jarvis.agent_runtimes import manager
from jarvis.agent_runtimes.base import RuntimeStatus


class Handle:
    turn_id = "turn-1"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class Driver:
    label = "Hermes"

    def __init__(self, ready: bool) -> None:
        self.ready = ready

    def detect(self, *, refresh: bool = False) -> RuntimeStatus:
        return RuntimeStatus("hermes", "Hermes", installed=self.ready, ready=self.ready)


@pytest.fixture(autouse=True)
def no_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_JOBS", {})
    monkeypatch.setattr(manager, "_TASKS", {})


async def test_a_ready_runtime_starts_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(*_args: Any, **_kwargs: Any) -> RuntimeStatus:
        raise AssertionError("a ready runtime needs no setup")

    monkeypatch.setattr(manager, "wait_ready", never)
    handle = Handle()
    await runner_acp._ready(handle, "hermes", Driver(ready=True))
    assert handle.events == []


async def test_a_missing_runtime_is_set_up_first(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = Driver(ready=False)

    async def install(name: str, **_kwargs: Any) -> RuntimeStatus:
        assert name == "hermes"
        driver.ready = True
        return driver.detect()

    monkeypatch.setattr(manager, "wait_ready", install)
    handle = Handle()
    await runner_acp._ready(handle, "hermes", driver)
    assert [event["kind"] for event in handle.events] == ["notice"]
    assert "Setting up Hermes" in handle.events[0]["payload"]["text"]


async def test_a_setup_that_fails_ends_the_turn_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = Driver(ready=False)

    async def fail(name: str, **_kwargs: Any) -> RuntimeStatus:
        failed = manager.RuntimeJob(name, "install", state="failed", message="no network")
        manager._JOBS[name] = failed
        return driver.detect()

    monkeypatch.setattr(manager, "wait_ready", fail)
    with pytest.raises(CliUnavailable, match="no network"):
        await runner_acp._ready(Handle(), "hermes", driver)
