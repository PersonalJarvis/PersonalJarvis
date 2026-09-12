"""Real journal/process recovery with a labeled test-only execution boundary.

No renderer, HTTP client or provider participates in the recovery process.
These fixtures prove local checkpoint recovery, not host sleep or live-provider
reconciliation after an external side effect.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import State

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.society.mars.models import (
    CommandState,
    DispatchRejected,
    ExecutionReceipt,
    StationCommand,
    StationError,
)
from jarvis.society.mars.store import MarsStore
from jarvis.ui.web.mars_routes import (
    _service,
    ensure_mars_station,
    schedule_mars_resume,
    stop_mars_station,
)


class FixtureRuntime:
    def __init__(self, data_dir: Path):
        self.store = SimpleNamespace(path=data_dir / "society.db")
        self.starting = asyncio.Event()
        self.ready = asyncio.Event()
        self.ready.set()

    async def ensure_started(self):
        self.starting.set()
        await self.ready.wait()


class FixtureExecutor:
    """Deterministic authority; never performs a provider call or external write."""

    def __init__(self, _runtime):
        self.dispatches = []
        self.inspections = []
        self.authorizations = []
        self.denied = False
        self.inspection_started = asyncio.Event()
        self.inspection_gate = asyncio.Event()
        self.inspection_gate.set()

    async def authorize(self, *, agent_id, capability_id):
        self.authorizations.append((agent_id, capability_id))
        if self.denied:
            raise DispatchRejected("permission_denied", 403)

    async def dispatch(self, *, agent_id, command_id, trace_id, draft):
        self.dispatches.append(command_id)
        return {
            "state": "completed",
            "task_ref": f"fixture-task:{command_id}",
            "result_ref": f"fixture-result:{command_id}",
        }

    async def inspect(self, *, agent_id, command_id, trace_id, task_ref):
        self.inspections.append(command_id)
        self.inspection_started.set()
        await self.inspection_gate.wait()
        return {
            "state": "completed",
            "task_ref": task_ref or f"fixture-task:{command_id}",
            "result_ref": f"fixture-result:{command_id}",
        }

    async def cancel(self, **_kwargs):
        raise AssertionError("Recovery cannot invent a cancellation request")


async def seed_journal(data_dir: Path, checkpoint: str):
    store = MarsStore(data_dir / "mars" / "ordinary.db")
    await store.open()
    command = await store.submit(
        "fixture-agent", StationCommand(request_id="fixture-request", draft="Fictional draft.")
    )
    if checkpoint != "queued":
        claimed = await store.claim_next()
        receipt = ExecutionReceipt(
            state=CommandState.COMPLETED if checkpoint == "completed" else CommandState.ACTIVE,
            task_ref=f"fixture-task:{command.command_id}",
            result_ref=f"fixture-result:{command.command_id}"
            if checkpoint == "completed"
            else None,
        )
        await store.apply(command.command_id, claimed.fence, receipt)
    record = await store.get(command.command_id)
    return store, record


def setup_state(data_dir: Path, monkeypatch):
    import jarvis.society.mars.ordinary as ordinary

    runtime = FixtureRuntime(data_dir)
    executor = FixtureExecutor(runtime)
    monkeypatch.setattr(ordinary, "OrdinaryStationExecutor", lambda _runtime: executor)
    calls = []

    def factory():
        calls.append("factory")
        return runtime

    return State({"society_factory": factory}), runtime, executor, calls


async def wait_terminal(service, command_id):
    async with asyncio.timeout(3):
        while True:
            record = await service.store.get(command_id)
            if record.state in {CommandState.COMPLETED, CommandState.FAILED}:
                return record
            await asyncio.sleep(0.01)


async def test_no_journal_does_not_construct_runtime_or_create_storage(tmp_path, monkeypatch):
    state, _runtime, _executor, calls = setup_state(tmp_path, monkeypatch)
    schedule_mars_resume(state, tmp_path)
    startup = state.mars_station_startup_task
    assert calls == []
    assert not (tmp_path / "mars").exists()
    await startup
    await stop_mars_station(state)
    assert calls == []
    assert not (tmp_path / "mars").exists()


async def test_queued_recovery_reauthorizes_before_dispatch(tmp_path, monkeypatch):
    store, command = await seed_journal(tmp_path, "queued")
    await store.close()
    state, _runtime, executor, calls = setup_state(tmp_path, monkeypatch)
    executor.denied = True
    try:
        schedule_mars_resume(state, tmp_path)
        await state.mars_station_startup_task
        record = await wait_terminal(state.mars_station, command.command_id)
        assert record.state is CommandState.FAILED
        assert record.reason == "permission_denied"
        assert executor.authorizations == [("fixture-agent", "communication-draft")]
        assert executor.dispatches == []
        assert calls == ["factory"]
    finally:
        await stop_mars_station(state)


async def test_boot_and_first_api_share_one_service_and_owner(tmp_path, monkeypatch):
    store, _command = await seed_journal(tmp_path, "queued")
    await store.close()
    state, runtime, _executor, calls = setup_state(tmp_path, monkeypatch)
    runtime.ready.clear()
    app = FastAPI()
    app.state = state
    request = Request({"type": "http", "app": app})
    try:
        schedule_mars_resume(state, tmp_path)
        startup = state.mars_station_startup_task
        await asyncio.wait_for(runtime.starting.wait(), 2)
        api = asyncio.create_task(_service(request))
        schedule_mars_resume(state, tmp_path)
        assert state.mars_station_startup_task is startup
        runtime.ready.set()
        await startup
        service = await api
        owner = state.mars_station_task
        assert service is state.mars_station
        assert await ensure_mars_station(state) is service
        assert state.mars_station_task is owner and not owner.done()
        assert calls == ["factory"]
        competing = MarsStore(service.store.path)
        with pytest.raises(StationError, match="world_owned_by_another_process"):
            await competing.open()
    finally:
        runtime.ready.set()
        await stop_mars_station(state)


@pytest.mark.parametrize("entry", ["boot", "api"])
async def test_stop_during_runtime_initialization_prevents_late_owner(tmp_path, monkeypatch, entry):
    store, _command = await seed_journal(tmp_path, "queued")
    await store.close()
    state, runtime, executor, _calls = setup_state(tmp_path, monkeypatch)
    runtime.ready.clear()
    if entry == "boot":
        schedule_mars_resume(state, tmp_path)
        pending = state.mars_station_startup_task
    else:
        pending = asyncio.create_task(ensure_mars_station(state))
    await asyncio.wait_for(runtime.starting.wait(), 2)
    stopping = asyncio.create_task(stop_mars_station(state))
    await asyncio.sleep(0)
    assert state.mars_station_stopping
    # Readiness never arrives. Stop owns and cancels initialization even when an
    # API caller, rather than deferred boot, triggered the first factory call.
    await asyncio.wait_for(stopping, 1)
    assert not runtime.ready.is_set()
    outcome = (await asyncio.gather(pending, return_exceptions=True))[0]
    assert isinstance(outcome, asyncio.CancelledError)
    assert getattr(state, "mars_station", None) is None
    assert state.mars_station_task is None
    assert state.mars_station_startup_task is None
    assert state.mars_station_initialization_task is None
    assert executor.dispatches == []
    schedule_mars_resume(state, tmp_path)
    assert state.mars_station_startup_task is None
    with pytest.raises(HTTPException, match="mars_station_stopped"):
        await ensure_mars_station(state)
    # Neither late initialization nor stop leaves the durable owner's file lock held.
    reopened = MarsStore(tmp_path / "mars" / "ordinary.db")
    await reopened.open()
    await reopened.close()


async def test_canceled_http_waiter_preserves_shared_initialization(tmp_path, monkeypatch):
    state, runtime, executor, calls = setup_state(tmp_path, monkeypatch)
    runtime.ready.clear()
    first = asyncio.create_task(ensure_mars_station(state))
    await asyncio.wait_for(runtime.starting.wait(), 2)
    initialization = state.mars_station_initialization_task
    second = asyncio.create_task(ensure_mars_station(state))
    await asyncio.sleep(0)
    try:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not initialization.done()
        assert state.mars_station_initialization_task is initialization
        runtime.ready.set()
        service = await asyncio.wait_for(second, 2)
        assert service is state.mars_station
        assert calls == ["factory"]
        assert executor.dispatches == []
        assert await ensure_mars_station(state) is service
    finally:
        await stop_mars_station(state)
        await asyncio.gather(first, second, return_exceptions=True)


async def test_initialization_deadline_is_shared_and_retryable(tmp_path, monkeypatch, caplog):
    import jarvis.ui.web.mars_routes as routes

    monkeypatch.setattr(routes, "_INITIALIZATION_TIMEOUT_S", 0.02)
    state, runtime, _executor, calls = setup_state(tmp_path, monkeypatch)
    runtime.ready.clear()
    first = asyncio.create_task(ensure_mars_station(state))
    second = asyncio.create_task(ensure_mars_station(state))
    outcomes = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
    assert all(isinstance(outcome, HTTPException) for outcome in outcomes)
    assert "TimeoutError" in caplog.text
    assert calls == ["factory"]
    assert getattr(state, "mars_station", None) is None
    assert state.mars_station_initialization_task.done()
    monkeypatch.setattr(routes, "_INITIALIZATION_TIMEOUT_S", 2.0)
    runtime.ready.set()
    try:
        # Existing runtime ensure_started is retryable; no second runtime is built.
        service = await ensure_mars_station(state)
        assert service is state.mars_station
        assert calls == ["factory"]
    finally:
        await stop_mars_station(state)


async def test_cancellation_ignoring_startup_cannot_publish_after_stop(tmp_path, monkeypatch):
    import jarvis.ui.web.mars_routes as routes

    state, runtime, executor, _calls = setup_state(tmp_path, monkeypatch)
    runtime.ready.clear()

    async def reluctant_start():
        runtime.starting.set()
        try:
            await runtime.ready.wait()
        except asyncio.CancelledError:
            # Test-only misbehaving collaborator. Production stop must remain
            # bounded and must fence this task even if it later finishes.
            await runtime.ready.wait()

    runtime.ensure_started = reluctant_start
    monkeypatch.setattr(routes, "_INITIALIZATION_STOP_TIMEOUT_S", 0.02)
    pending = asyncio.create_task(ensure_mars_station(state))
    await asyncio.wait_for(runtime.starting.wait(), 2)
    try:
        with pytest.raises(TimeoutError, match="mars_station_shutdown_timeout"):
            await asyncio.wait_for(stop_mars_station(state), 1)
        assert state.mars_station_stopping
        assert not state.mars_station_initialization_task.done()
        runtime.ready.set()
        with pytest.raises(HTTPException):
            await asyncio.wait_for(pending, 1)
        assert getattr(state, "mars_station", None) is None
        assert executor.dispatches == []
        assert not (tmp_path / "mars").exists()
    finally:
        runtime.ready.set()
        await stop_mars_station(state)
        await asyncio.gather(pending, return_exceptions=True)


async def test_server_stop_finishes_independent_cleanup_after_mars_timeout(tmp_path, monkeypatch):
    import jarvis.clis.shared as cli_shared
    import jarvis.codex_app_server as codex_servers
    import jarvis.marketplace.plugin_shared as plugin_shared
    import jarvis.ui.web.mars_routes as routes
    from jarvis.ui.web.server import WebServer

    state, runtime, executor, _calls = setup_state(tmp_path, monkeypatch)
    runtime.ready.clear()
    cleaned = []

    async def reluctant_start():
        runtime.starting.set()
        try:
            await runtime.ready.wait()
        except asyncio.CancelledError:
            # This misbehaving fixture stays unresolved throughout server.stop().
            await runtime.ready.wait()

    def sync_cleanup(name):
        return lambda *_args, **_kwargs: cleaned.append(name)

    def async_cleanup(name):
        async def action(*_args, **_kwargs):
            cleaned.append(name)

        return action

    runtime.ensure_started = reluctant_start
    runtime.browser = SimpleNamespace(close=async_cleanup("society-browser"))
    monkeypatch.setattr(routes, "_INITIALIZATION_STOP_TIMEOUT_S", 0.02)
    monkeypatch.setattr(cli_shared, "set_active_registry", sync_cleanup("cli-registry"))
    monkeypatch.setattr(
        plugin_shared, "set_active_plugin_registry", sync_cleanup("plugin-registry-handle")
    )
    monkeypatch.setattr(
        codex_servers, "close_shared_codex_app_servers", async_cleanup("codex-servers")
    )
    pending = asyncio.create_task(ensure_mars_station(state))
    await asyncio.wait_for(runtime.starting.wait(), 2)

    # Bypass boot composition only: the entire production WebServer.stop body
    # runs with explicit test-only resource handles and the real Mars lifecycle.
    server = WebServer.__new__(WebServer)
    server.app = SimpleNamespace(state=state)
    for name in (
        "_board_aggregator_task",
        "_realtime_warm_task",
        "_bio_scheduler",
        "_board_evaluator",
        "_board_aggregator",
        "_task_scheduler",
        "_task_cancel_token",
        "_task_scheduler_task",
        "_task_store",
    ):
        setattr(server, name, None)
    server._mic_level_sessions = {"fixture-session"}
    server._stop_mic_level_bridge = sync_cleanup("mic-bridge")
    server._stop_marketplace_refresh_scheduler = async_cleanup("marketplace-refresh")
    server._stop_local_models_health_monitor = async_cleanup("local-model-health")
    server._stop_local_models_autostart = async_cleanup("local-model-autostart")
    server._skill_registry = SimpleNamespace(stop_watcher=sync_cleanup("skill-watcher"))
    server._doc_registry = SimpleNamespace(close=sync_cleanup("doc-watcher"))
    server._plugin_registry = SimpleNamespace(stop=async_cleanup("plugins"))
    server._pty = SimpleNamespace(close_all=sync_cleanup("pty"))
    server._mission_tool_approvals = SimpleNamespace(deny_all=async_cleanup("mission-approvals"))
    state.agent_chat = SimpleNamespace(cancel_all=async_cleanup("chat"))
    state.mission_manager = SimpleNamespace(stop=async_cleanup("missions"))

    browser_started = asyncio.Event()

    async def prepare_browser():
        browser_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append("browser-prepare")

    server._browser_prepare_task = asyncio.create_task(prepare_browser())
    await browser_started.wait()
    server_exit = asyncio.Event()

    class FixtureUvicorn:
        @property
        def should_exit(self):
            return server_exit.is_set()

        @should_exit.setter
        def should_exit(self, value):
            if value:
                cleaned.append("uvicorn-exit")
                server_exit.set()

    uvicorn = server._server = FixtureUvicorn()
    serving = server._serve_task = asyncio.create_task(server_exit.wait())
    try:
        with pytest.raises(
            RuntimeError, match=r"mars_station_shutdown_incomplete \(TimeoutError\)"
        ):
            await asyncio.wait_for(server.stop(), 2)
        assert {
            "browser-prepare",
            "society-browser",
            "chat",
            "marketplace-refresh",
            "local-model-health",
            "local-model-autostart",
            "skill-watcher",
            "doc-watcher",
            "plugins",
            "pty",
            "mission-approvals",
            "missions",
            "uvicorn-exit",
            "codex-servers",
        } <= set(cleaned)
        assert server._browser_prepare_task is None
        assert server._plugin_registry is None
        assert server._server is server._serve_task is None
        assert uvicorn.should_exit and serving.done()
        assert state.mars_station_stopping
        assert not runtime.ready.is_set()
        assert not state.mars_station_initialization_task.done()
        runtime.ready.set()
        with pytest.raises(HTTPException):
            await asyncio.wait_for(pending, 1)
        assert getattr(state, "mars_station", None) is None
        assert executor.dispatches == []
    finally:
        runtime.ready.set()
        server_exit.set()
        await stop_mars_station(state)
        await asyncio.gather(pending, serving, return_exceptions=True)


async def test_stop_joins_inspection_before_closing_journal(tmp_path, monkeypatch):
    store, command = await seed_journal(tmp_path, "active")
    await store.close()
    state, _runtime, executor, _calls = setup_state(tmp_path, monkeypatch)
    executor.inspection_gate.clear()
    schedule_mars_resume(state, tmp_path)
    await state.mars_station_startup_task
    owner = state.mars_station_task
    await asyncio.wait_for(executor.inspection_started.wait(), 2)
    await stop_mars_station(state)
    assert owner.done()
    assert executor.dispatches == []
    reopened = MarsStore(tmp_path / "mars" / "ordinary.db")
    try:
        await reopened.open()
        record = await reopened.get(command.command_id)
        assert record.state is CommandState.INTERRUPTED
        assert record.result_ref is None
    finally:
        await reopened.close()


async def test_recovery_errors_log_only_exception_type(tmp_path, caplog):
    store, _command = await seed_journal(tmp_path, "queued")
    await store.close()

    def failing_factory():
        raise ValueError("PRIVATE-DRAFT-OR-PROVIDER-BODY")

    state = State({"society_factory": failing_factory})
    schedule_mars_resume(state, tmp_path)
    await state.mars_station_startup_task
    await stop_mars_station(state)
    assert "ValueError" in caplog.text
    assert "PRIVATE-DRAFT-OR-PROVIDER-BODY" not in caplog.text
    assert getattr(state, "mars_station", None) is None


async def child_recover(data_dir: Path):
    import jarvis.society.mars.ordinary as ordinary

    seed = json.loads((data_dir / "seed.json").read_text("utf-8"))
    runtime = FixtureRuntime(data_dir)
    executor = FixtureExecutor(runtime)
    ordinary.OrdinaryStationExecutor = lambda _runtime: executor
    state = State({"society_factory": lambda: runtime})
    try:
        schedule_mars_resume(state, data_dir)
        await state.mars_station_startup_task
        record = await wait_terminal(state.mars_station, seed["command_id"])
        result = record.model_dump(mode="json")
        result.update(dispatches=executor.dispatches, inspections=executor.inspections)
        (data_dir / "recovered.json").write_text(json.dumps(result), "utf-8")
    finally:
        await stop_mars_station(state)


@pytest.mark.parametrize("checkpoint", ["queued", "active", "completed"])
def test_abrupt_process_exit_recovers_without_http_clients(tmp_path, checkpoint):
    def child(*args):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *args, str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW_CREATIONFLAGS,
            timeout=25,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    child("seed", checkpoint)
    child("recover")
    seed = json.loads((tmp_path / "seed.json").read_text("utf-8"))
    recovered = json.loads((tmp_path / "recovered.json").read_text("utf-8"))
    assert recovered["command_id"] == seed["command_id"]
    assert recovered["trace_id"] == seed["trace_id"]
    assert recovered["state"] == "completed"
    assert recovered["result_ref"] == f"fixture-result:{seed['command_id']}"
    if checkpoint == "queued":
        assert recovered["dispatches"] == [seed["command_id"]]
        assert recovered["inspections"] == []
    elif checkpoint == "active":
        assert recovered["dispatches"] == []
        assert recovered["inspections"] == [seed["command_id"]]
        assert recovered["fence"] > seed["fence"]
        assert recovered["task_ref"] == seed["task_ref"]
    else:
        assert recovered["dispatches"] == recovered["inspections"] == []
        assert recovered["task_ref"] == seed["task_ref"]


if __name__ == "__main__":
    child_dir = Path(sys.argv[-1])
    if sys.argv[1] == "seed":

        async def seed_and_exit():
            _store, record = await seed_journal(child_dir, sys.argv[2])
            (child_dir / "seed.json").write_text(record.model_dump_json(), "utf-8")
            # Model an abrupt owner process exit after the SQLite commit, with
            # no service.close(), connection close, or owner-lock cleanup.
            os._exit(0)

        asyncio.run(seed_and_exit())
    else:
        asyncio.run(child_recover(child_dir))
