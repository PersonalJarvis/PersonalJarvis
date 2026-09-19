"""Production navigation HTTP, authority and lifecycle with real local journals.

The runtime shell opens the real roster only. Station work uses an explicitly
labeled fixture executor, never a provider or an external operation.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from starlette.datastructures import State

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.society.mars.definition import load_definition
from jarvis.society.mars.models import StationCommand
from jarvis.society.mars.navigation_models import MoveCommand, NavigationState
from jarvis.society.mars.navigation_ordinary import OrdinaryNavigationAuthority
from jarvis.society.mars.navigation_service import MarsNavigationService
from jarvis.society.mars.navigation_store import MarsNavigationStore
from jarvis.society.roster import Roster
from jarvis.society.store import SocietyStore
from jarvis.ui.web.mars_routes import (
    ensure_mars_navigation,
    ensure_mars_station,
    router,
    schedule_mars_resume,
    stop_mars_station,
)
from jarvis.ui.web.surface_security import SurfaceSecurity

pytestmark = pytest.mark.no_auto_web_auth

BASE = "http://127.0.0.1:47821"
PREFIX = "/api/society/mars"
CREDENTIAL = "navigation-fixture-control-key"
HEADERS = {"Authorization": f"Bearer {CREDENTIAL}", "Origin": BASE}


class RosterRuntime:
    """Local fixture shell: real ordinary identity storage, no task runtime."""

    def __init__(self, data_dir):
        self.store = SocietyStore(data_dir / "society.db")
        self.roster = Roster(self.store)
        self.ready = asyncio.Event()
        self.ready.set()
        self.starting = asyncio.Event()
        self.started = False

    async def ensure_started(self):
        self.starting.set()
        await self.ready.wait()
        if not self.started:
            await self.store.open()
            for name in ("One", "Two"):
                await self.roster.create(name=name)
            self.started = True

    def chat_service(self):
        raise AssertionError("A visit must not open a chat or call a provider")


class FixtureStationExecutor:
    """Explicit task boundary for testing independence from a slow inspection."""

    def __init__(self, _runtime):
        self.dispatched = []
        self.inspection_started = asyncio.Event()
        self.inspection_gate = asyncio.Event()

    async def authorize(self, **_kwargs):
        return None

    async def dispatch(self, *, command_id, **_kwargs):
        self.dispatched.append(command_id)
        return {"state": "active", "task_ref": "fixture-task:" + command_id}

    async def inspect(self, *, task_ref, **_kwargs):
        self.inspection_started.set()
        await self.inspection_gate.wait()
        return {"state": "active", "task_ref": task_ref}

    async def cancel(self, **_kwargs):
        raise AssertionError("Stopping navigation must never cancel ordinary work")


def shell(data_dir, monkeypatch):
    import jarvis.society.mars.ordinary as ordinary

    runtime = RosterRuntime(data_dir)
    executor = FixtureStationExecutor(runtime)
    monkeypatch.setattr(ordinary, "OrdinaryStationExecutor", lambda _runtime: executor)
    calls = []

    def factory():
        calls.append("factory")
        return runtime

    return State({"society_factory": factory}), runtime, executor, calls


@pytest.fixture
async def world(tmp_path, monkeypatch):
    state, runtime, executor, calls = shell(tmp_path, monkeypatch)
    app = FastAPI()
    app.state = state
    app.include_router(router)
    security = SurfaceSecurity(
        app,
        control_key_validator=lambda value: value == CREDENTIAL,
        session_validator=lambda _value: False,
    )

    def client():
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=security), base_url=BASE)

    try:
        yield SimpleNamespace(
            app=app, runtime=runtime, executor=executor, calls=calls, client=client
        )
    finally:
        await stop_mars_station(state)
        await runtime.store.close()


def body(request_id="visit", destination="communications-console", **changes):
    return {"request_id": request_id, "station_id": destination, **changes}


async def post(client, agent="one", **kwargs):
    return await client.post(f"{PREFIX}/agents/{agent}/moves", headers=HEADERS, json=body(**kwargs))


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Origin": BASE}, 401),
        ({**HEADERS, "Authorization": "Bearer invalid-fixture"}, 401),
        ({**HEADERS, "Origin": "https://foreign.invalid"}, 403),
    ],
)
async def test_authentication_precedes_navigation_initialization(world, headers, status):
    async with world.client() as client:
        read = await client.get(f"{PREFIX}/navigation/snapshot", headers=headers)
        write = await client.post(f"{PREFIX}/agents/one/moves", headers=headers, json=body())
        cancel = await client.post(f"{PREFIX}/agents/one/moves/unknown/cancel", headers=headers)
    assert read.status_code == write.status_code == cancel.status_code == status
    assert world.calls == []
    assert not world.runtime.store.path.exists()


@pytest.mark.parametrize("state", ["invented", "archived", "kill_switch"])
async def test_real_roster_and_execution_stop_control_movement(world, state):
    service = await ensure_mars_navigation(world.app.state)
    agent = "one"
    if state == "invented":
        agent = "fabricated-worker"
    elif state == "archived":
        await world.runtime.roster.update(agent, {"state": "archived"})
    else:
        await world.runtime.store.set_kill_switch(True)
    async with world.client() as client:
        response = await post(client, agent)
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "navigation_not_authorized"
    assert (await service.snapshot()).commands == ()
    assert world.executor.dispatched == []


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"mode": "rover"}, "unsupported_navigation_mode"),
        ({"mode": "spaceship"}, "unsupported_navigation_mode"),
        ({"world_id": "mars:another"}, "invalid_navigation_request"),
        ({"graph_version": True}, "invalid_navigation_request"),
        ({"position": [1, 2, 3]}, "invalid_navigation_request"),
        ({"agent_id": "forged"}, "invalid_navigation_request"),
        ({"draft": "private communication"}, "invalid_navigation_request"),
    ],
)
async def test_public_api_rejects_unsupported_mode_or_forged_body_before_initialization(
    world, patch, reason
):
    async with world.client() as client:
        response = await post(client, **patch)
    assert response.status_code == 422
    assert response.json() == {"detail": {"reason": reason}}
    assert world.calls == []


@pytest.mark.parametrize("location", ["extra_value", "extra_key", "body", "actor_path"])
async def test_private_rejected_input_is_never_echoed_or_logged(world, location, caplog):
    private = "sk-proj-" + "A1" * 20
    payload = body()
    actor = "one"
    if location == "extra_value":
        payload["extra"] = private
    elif location == "extra_key":
        payload[private] = True
    elif location == "body":
        payload = private
    else:
        actor = private + "@invalid"
    async with world.client() as client:
        response = await client.post(
            f"{PREFIX}/agents/{actor}/moves", headers=HEADERS, json=payload
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"reason": "invalid_navigation_request"}}
    assert response.headers["cache-control"] == "no-store"
    assert private not in response.text and private not in caplog.text
    assert world.calls == []


async def test_http_clients_share_move_identity_and_only_known_destinations(world):
    async with world.client() as one, world.client() as two:
        first, second = await asyncio.gather(post(one), post(two))
        assert first.status_code == second.status_code == 200
        assert first.json()["command_id"] == second.json()["command_id"]
        conflict = await post(one, destination="outpost-bridge-staging")
        unknown = await post(one, request_id="other", destination="invented-place")
        stale = await post(one, request_id="other", graph_version=2)
        assert conflict.status_code == stale.status_code == 409
        assert unknown.status_code == 404
        snapshot = await one.get(f"{PREFIX}/navigation/snapshot", headers=HEADERS)
    assert len(snapshot.json()["commands"]) == 1
    assert not {"draft", "task_ref", "result_ref"} & snapshot.json()["commands"][0].keys()
    assert world.executor.dispatched == []
    assert world.calls == ["factory"]


async def test_move_cancel_leaves_actual_task_and_other_actor_unchanged(world):
    station = await ensure_mars_station(world.app.state)
    task = await station.submit("one", StationCommand(request_id="work", draft="Fixture draft."))
    async with asyncio.timeout(2):
        while (await station.store.get(task.command_id)).state.value != "active":  # noqa: ASYNC110 - wait for the real station coordinator's persisted dispatch receipt
            await asyncio.sleep(0.01)
    before = await station.store.get(task.command_id)
    async with world.client() as client:
        created = await post(client)
        command = created.json()["command_id"]
        foreign = await client.post(f"{PREFIX}/agents/two/moves/{command}/cancel", headers=HEADERS)
        assert foreign.status_code == 404
        canceled = await client.post(f"{PREFIX}/agents/one/moves/{command}/cancel", headers=HEADERS)
        repeated = await client.post(f"{PREFIX}/agents/one/moves/{command}/cancel", headers=HEADERS)
        assert canceled.json() == repeated.json()
        assert canceled.json()["state"] == "canceled"
    assert await station.store.get(task.command_id) == before
    assert world.executor.dispatched == [task.command_id]


async def test_canonical_two_agent_handoff_waits_for_safe_departure(world):
    service = await ensure_mars_navigation(world.app.state)
    # Keep HTTP/storage/graph/authority production code; replace only wall clock
    # so the 40-metre journey can be verified deterministically without sleeping.
    owner = world.app.state.mars_navigation_task
    owner.cancel()
    await asyncio.gather(owner, return_exceptions=True)
    now = [100_000]
    service.store.clock = lambda: now[0]

    async def advance(count):
        for _ in range(count):
            now[0] += 1000
            await service.advance()
            occupancy = (await service.snapshot()).occupancies
            assert len({row.resource_id for row in occupancy}) == len(occupancy)

    async with world.client() as client:
        first = (await post(client)).json()["command_id"]
        second = (await post(client, "two")).json()["command_id"]
        await advance(25)
        assert (await service.store.get(first)).state is NavigationState.ARRIVED
        waiting = await service.store.get(second)
        assert waiting.state is NavigationState.QUEUEING and waiting.presence == "spawn_queue"
        departure = await post(client, request_id="depart", destination="outpost-bridge-staging")
        assert departure.status_code == 200
        departing_id = departure.json()["command_id"]
        await advance(65)
        assert (await service.store.get(departing_id)).state is NavigationState.ARRIVED
        assert (await service.store.get(second)).state is NavigationState.ARRIVED
        visits = (await client.get(f"{PREFIX}/navigation/snapshot", headers=HEADERS)).json()
    latest = {row["agent_id"]: row for row in visits["commands"]}
    assert latest["one"]["current_node"] == "outpost-west"
    assert latest["two"]["current_node"] == "console"
    assert world.executor.dispatched == []


async def test_slow_station_inspection_cannot_block_zero_client_navigation(world):
    station = await ensure_mars_station(world.app.state)
    await station.submit("one", StationCommand(request_id="work", draft="Fixture draft."))
    async with world.client() as client:
        command = (await post(client)).json()["command_id"]
    await asyncio.wait_for(world.executor.inspection_started.wait(), 3)
    navigation = world.app.state.mars_navigation
    before = await navigation.store.get(command)
    async with asyncio.timeout(2):
        while (await navigation.store.get(command)).position == before.position:  # noqa: ASYNC110 - observe committed backend progress without any HTTP client
            await asyncio.sleep(0.02)
    assert not world.executor.inspection_gate.is_set()
    assert not world.app.state.mars_station_task.done()
    assert not world.app.state.mars_navigation_task.done()


async def test_both_surfaces_share_initialization_and_canceled_waiter_does_not_own_it(world):
    state = world.app.state
    world.runtime.ready.clear()
    first = asyncio.create_task(ensure_mars_navigation(state))
    await world.runtime.starting.wait()
    second = asyncio.create_task(ensure_mars_station(state))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not state.mars_station_initialization_task.done()
    world.runtime.ready.set()
    station = await second
    assert station is state.mars_station
    assert await ensure_mars_navigation(state) is state.mars_navigation
    assert world.calls == ["factory"]
    assert state.mars_navigation_task.get_name() == "mars-navigation-owner"


async def test_stop_fences_both_surfaces_and_releases_both_journal_owners(world):
    state = world.app.state
    navigation = await ensure_mars_navigation(state)
    station = state.mars_station
    tasks = [state.mars_navigation_task, state.mars_station_task]
    await stop_mars_station(state)
    assert all(task.done() for task in tasks)
    assert state.mars_station is state.mars_navigation is None
    with pytest.raises(HTTPException):
        await ensure_mars_navigation(state)
    await navigation.store.open()
    await station.store.open()
    await navigation.store.close()
    await station.store.close()


async def test_navigation_journal_alone_resumes_without_http_client(tmp_path, monkeypatch):
    state, runtime, _executor, calls = shell(tmp_path, monkeypatch)
    await runtime.ensure_started()
    navigation = MarsNavigationService(
        MarsNavigationStore(tmp_path / "mars" / "navigation.db"),
        load_definition(),
        authorize=OrdinaryNavigationAuthority(runtime),
    )
    await navigation.start()
    command = await navigation.submit("one", MoveCommand(**body()))
    await navigation.close()
    assert not (tmp_path / "mars" / "ordinary.db").exists()
    try:
        schedule_mars_resume(state, tmp_path)
        await state.mars_station_startup_task
        resumed = state.mars_navigation
        async with asyncio.timeout(2):
            while (await resumed.store.get(command.command_id)).position == command.position:  # noqa: ASYNC110 - verify the owned loop, no HTTP client exists
                await asyncio.sleep(0.02)
        assert calls == ["factory"]
    finally:
        await stop_mars_station(state)
        await runtime.store.close()


def test_navigation_openapi_models_are_typed_and_dynamic_cli_reaches_routes():
    from jarvis.cli_ctl.dynamic import build_api_group

    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    command = schema["components"]["schemas"]["PedestrianMoveCommand"]
    assert command["properties"]["mode"]["const"] == "pedestrian"
    assert set(command["required"]) == {"request_id", "station_id"}
    calls = []

    def runner(method, path, params, body, **kwargs):
        calls.append((method, path, params, body))
        return {"ok": True}

    cli = build_api_group(schema, runner)
    for args in (
        ["get-mars-navigation-snapshot"],
        ["submit-mars-move", "--agent_id", "one", "--json-body", json.dumps(body()), "--yes"],
        ["cancel-mars-move", "--agent_id", "one", "--command_id", "visit", "--yes"],
    ):
        result = CliRunner().invoke(cli, ["mars", *args])
        assert result.exit_code == 0, result.output
    assert [(method, path) for method, path, *_ in calls] == [
        ("get", f"{PREFIX}/navigation/snapshot"),
        ("post", f"{PREFIX}/agents/one/moves"),
        ("post", f"{PREFIX}/agents/one/moves/visit/cancel"),
    ]
    assert calls[1][3] == body()


async def child_process(data_dir, action):
    runtime = RosterRuntime(data_dir)
    await runtime.ensure_started()
    if action == "seed":
        navigation = MarsNavigationService(
            MarsNavigationStore(data_dir / "mars" / "navigation.db"),
            load_definition(),
            authorize=OrdinaryNavigationAuthority(runtime),
        )
        await navigation.start()
        command = await navigation.submit("one", MoveCommand(**body()))
        (data_dir / "seed.json").write_text(command.model_dump_json(), "utf-8")
        os._exit(0)
    state = State({"society_factory": lambda: runtime})
    seed = json.loads((data_dir / "seed.json").read_text("utf-8"))
    try:
        schedule_mars_resume(state, data_dir)
        await state.mars_station_startup_task
        service = state.mars_navigation
        async with asyncio.timeout(3):
            while True:
                command = await service.store.get(seed["command_id"])
                if command.position != tuple(seed["position"]):
                    break
                await asyncio.sleep(0.02)
        (data_dir / "recovered.json").write_text(command.model_dump_json(), "utf-8")
    finally:
        await stop_mars_station(state)
        await runtime.store.close()


def test_abrupt_process_exit_navigation_resumes_with_no_renderer_or_client(tmp_path):
    for action in ("seed", "recover"):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), action, str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW_CREATIONFLAGS,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    seed = json.loads((tmp_path / "seed.json").read_text("utf-8"))
    recovered = json.loads((tmp_path / "recovered.json").read_text("utf-8"))
    assert recovered["command_id"] == seed["command_id"]
    assert recovered["trace_id"] == seed["trace_id"]
    assert recovered["position"] != seed["position"]
    assert recovered["presence"] == "placed"


if __name__ == "__main__":
    asyncio.run(child_process(Path(sys.argv[2]), sys.argv[1]))
