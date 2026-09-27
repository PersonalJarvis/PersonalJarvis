"""Authenticated Mars HTTP contracts with real storage and a test-only executor.

No provider is called. These prove request, authority, privacy and backend-loop
behavior; they do not stand in for live-provider, desktop-close or visual acceptance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from jarvis.society.mars.models import CommandState, DispatchRejected
from jarvis.society.mars.service import MarsStationService
from jarvis.society.mars.store import MarsStore
from jarvis.ui.web.mars_routes import _reconcile_loop, router
from jarvis.ui.web.surface_security import SurfaceSecurity

pytestmark = pytest.mark.no_auto_web_auth

_BASE = "http://127.0.0.1:47821"
_PREFIX = "/api/society/mars"
_TEST_CREDENTIAL = "mars-http-fixture-credential"  # noqa: S105 - test-only credential
_HEADERS = {"Authorization": f"Bearer {_TEST_CREDENTIAL}", "Origin": _BASE}


class FixtureExecutor:
    """Test-only authority; completion changes only on explicit fixture evidence."""

    def __init__(self) -> None:
        self.dispatches: list[str] = []
        self.cancellations: list[tuple[str, str]] = []
        self.completed = asyncio.Event()
        self.dispatched = asyncio.Event()
        self.records: dict[str, dict] = {}
        self.allow_credentials = True

    async def authorize(self, *, agent_id: str, capability_id: str) -> None:
        if agent_id not in {"one", "two"} or capability_id != "communication-draft":
            raise DispatchRejected("permission_denied", 403)

    async def dispatch(self, *, agent_id, command_id, trace_id, draft):
        self.dispatches.append(command_id)
        self.records[command_id] = {"state": "active", "task_ref": f"fixture-task:{command_id}"}
        self.dispatched.set()
        return self.records[command_id]

    async def inspect(self, *, agent_id, command_id, trace_id, task_ref):
        current = self.records[command_id]
        if self.completed.is_set() and current["state"] == "active":
            current = {
                "state": "completed",
                "task_ref": current["task_ref"],
                "result_ref": f"fixture-result:{command_id}",
            }
            self.records[command_id] = current
        return current

    async def cancel(self, *, agent_id, command_id, trace_id, task_ref):
        self.cancellations.append((agent_id, command_id))
        self.records[command_id] = {"state": "canceled", "task_ref": task_ref}
        return {**self.records[command_id], "cancel_attempt": "attempted"}


@dataclass
class HttpWorld:
    app: FastAPI
    secured: SurfaceSecurity
    service: MarsStationService
    executor: FixtureExecutor

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.secured),
            base_url=_BASE,
        )


@pytest.fixture
async def world(tmp_path):
    executor = FixtureExecutor()
    service = MarsStationService(MarsStore(tmp_path / "mars.db"), executor)
    await service.start()
    app = FastAPI()
    app.include_router(router)
    # Inject only the executor-bearing composition. All HTTP handlers, storage,
    # station coordination and the outer authentication boundary are production code.
    app.state.mars_station = service
    owner = asyncio.create_task(_reconcile_loop(service), name="mars-http-test-owner")
    app.state.mars_station_task = owner
    secured = SurfaceSecurity(
        app,
        control_key_validator=lambda value: (
            executor.allow_credentials and value == _TEST_CREDENTIAL
        ),
        session_validator=lambda _value: False,
    )
    try:
        yield HttpWorld(app, secured, service, executor)
    finally:
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        await service.close()


def _body(request_id="one", **changes):
    return {"request_id": request_id, "draft": "Private draft for operator review.", **changes}


async def _wait_for_state(world: HttpWorld, command_id: str, state: CommandState):
    async with asyncio.timeout(3):
        while (await world.service.store.get(command_id)).state is not state:  # noqa: ASYNC110 - observe committed state, not an executor signal
            await asyncio.sleep(0.01)


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Origin": _BASE}, 401),
        ({"Origin": _BASE, "Authorization": "Bearer wrong-fixture"}, 401),
        ({**_HEADERS, "Origin": "https://foreign.invalid"}, 403),
        ({**_HEADERS, "Origin": "null"}, 403),
    ],
)
async def test_missing_credentials_and_foreign_origins_never_reach_station(world, headers, status):
    async with world.client() as client:
        response = await client.post(
            f"{_PREFIX}/agents/one/commands", headers=headers, json=_body()
        )
        snapshot = await client.get(f"{_PREFIX}/snapshot", headers=headers)
    assert response.status_code == status and snapshot.status_code == status
    assert (await world.service.store.snapshot()).seq == 0
    assert world.executor.dispatches == []


async def test_revoked_credential_cannot_read_existing_private_state(world):
    async with world.client() as client:
        response = await client.post(
            f"{_PREFIX}/agents/one/commands", headers=_HEADERS, json=_body()
        )
        assert response.status_code == 200
        world.executor.allow_credentials = False
        denied = await client.get(f"{_PREFIX}/snapshot", headers=_HEADERS)
    assert denied.status_code == 401
    assert response.json()["command_id"] not in denied.text


async def test_authenticated_request_cannot_invent_an_authorized_agent(world):
    async with world.client() as client:
        denied = await client.post(
            f"{_PREFIX}/agents/invented/commands", headers=_HEADERS, json=_body()
        )
    assert denied.status_code == 403
    assert denied.json()["detail"]["reason"] == "permission_denied"
    assert (await world.service.store.snapshot()).seq == 0
    assert not world.executor.dispatches


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"layout_version": 2},
        {"world_id": "mars:swarm:other"},
        {"transform": [0, 1, 2]},
        {"agent_id": "forged"},
        {"capability_id": "send-email"},
    ],
)
async def test_stale_and_malformed_station_requests_reject_without_mutation(world, change):
    async with world.client() as client:
        response = await client.post(
            f"{_PREFIX}/agents/one/commands",
            headers=_HEADERS,
            json=_body(**change),
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"reason": "invalid_station_request"}}
    assert (await world.service.store.snapshot()).seq == 0
    assert not world.executor.dispatches


@pytest.mark.parametrize("failure", ["version", "length", "extra", "field_name", "body_type"])
async def test_validation_failures_do_not_echo_credential_input(world, failure, caplog):
    credential = "sk-proj-" + "A1" * 20  # Deliberately synthetic shape.
    if failure == "version":
        body = _body(schema_version=2, draft=credential)
    elif failure == "length":
        body = _body(draft=credential + " prose" * 1000)
    elif failure == "extra":
        body = _body(unexpected_private_field=credential)
    elif failure == "field_name":
        body = {**_body(), credential: "unexpected key"}
    else:
        body = credential
    async with world.client() as client:
        response = await client.post(f"{_PREFIX}/agents/one/commands", headers=_HEADERS, json=body)
    assert response.status_code == 422
    assert credential not in response.text and credential not in caplog.text
    assert response.json() == {"detail": {"reason": "invalid_station_request"}}
    assert response.headers["cache-control"] == "no-store"
    assert (await world.service.store.snapshot()).seq == 0
    assert not world.executor.dispatches


async def test_credential_guard_returns_only_settings_direction_after_valid_schema(world, caplog):
    credential = "sk-proj-" + "A1" * 20
    async with world.client() as client:
        response = await client.post(
            f"{_PREFIX}/agents/one/commands",
            headers=_HEADERS,
            json=_body(draft="Please use this key " + credential),
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"reason": "credential_input_use_api_key_settings"}}
    assert credential not in response.text and credential not in caplog.text
    assert (await world.service.store.snapshot()).seq == 0


async def test_event_query_validation_is_bounded_and_does_not_echo_private_cursor(world):
    credential = "sk-proj-" + "A1" * 20
    async with world.client() as client:
        bad_limit = await client.get(f"{_PREFIX}/events?limit=201", headers=_HEADERS)
        bad_cursor = await client.get(
            f"{_PREFIX}/events", headers=_HEADERS, params={"after_seq": credential}
        )
    assert bad_limit.status_code == 422 and bad_cursor.status_code == 422
    assert credential not in bad_cursor.text
    assert bad_cursor.json() == {"detail": {"reason": "invalid_station_request"}}


async def test_two_http_clients_share_idempotency_and_bounded_delivered_cursor(world):
    async with world.client() as first, world.client() as second:
        submitted = await asyncio.gather(
            first.post(f"{_PREFIX}/agents/one/commands", headers=_HEADERS, json=_body()),
            second.post(f"{_PREFIX}/agents/one/commands", headers=_HEADERS, json=_body()),
        )
        assert all(response.status_code == 200 for response in submitted)
        command_id = submitted[0].json()["command_id"]
        assert submitted[1].json()["command_id"] == command_id
        await world.service.reconcile()
        mismatch = await first.post(
            f"{_PREFIX}/agents/one/commands",
            headers=_HEADERS,
            json=_body(draft="Different request content."),
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["detail"]["reason"] == "idempotency_payload_mismatch"
        snapshot = await first.get(f"{_PREFIX}/snapshot", headers=_HEADERS)
        page = (await first.get(f"{_PREFIX}/events?after_seq=0&limit=1", headers=_HEADERS)).json()
        assert len(page["events"]) == 1
        assert page["next_cursor"] == page["events"][0]["seq"] < page["latest_seq"]
        assert page["has_more"] and not page["resync_required"]
        next_page = (
            await first.get(
                f"{_PREFIX}/events?after_seq={page['next_cursor']}&limit=1",
                headers=_HEADERS,
            )
        ).json()
        assert next_page["events"][0]["seq"] == page["next_cursor"] + 1
    assert len(world.executor.dispatches) == 1
    assert len(snapshot.json()["commands"]) == 1
    assert "draft" not in snapshot.json()["commands"][0]
    assert _body()["draft"] not in snapshot.text
    assert _body()["draft"] not in str(page)


async def test_cancel_is_scoped_to_command_agent_and_does_not_repeat(world):
    async with world.client() as client:
        created = await client.post(
            f"{_PREFIX}/agents/one/commands", headers=_HEADERS, json=_body()
        )
        command_id = created.json()["command_id"]
        await _wait_for_state(world, command_id, CommandState.ACTIVE)
        foreign = await client.post(
            f"{_PREFIX}/agents/two/commands/{command_id}/cancel",
            headers=_HEADERS,
        )
        assert foreign.status_code == 404 and not world.executor.cancellations
        own = await client.post(
            f"{_PREFIX}/agents/one/commands/{command_id}/cancel",
            headers=_HEADERS,
        )
        assert own.status_code == 200
        await _wait_for_state(world, command_id, CommandState.CANCELED)
        repeated = await client.post(
            f"{_PREFIX}/agents/one/commands/{command_id}/cancel",
            headers=_HEADERS,
        )
        assert repeated.status_code == 200 and repeated.json()["state"] == "canceled"
    assert world.executor.cancellations == [("one", command_id)]


async def test_process_reconcile_finishes_after_every_http_client_closes(world):
    owner = world.app.state.mars_station_task
    async with world.client() as client:
        created = await client.post(
            f"{_PREFIX}/agents/one/commands", headers=_HEADERS, json=_body()
        )
        command_id = created.json()["command_id"]
        await asyncio.wait_for(world.executor.dispatched.wait(), timeout=2)
    # Every HTTP client is now closed. Only the production backend owner loop
    # can reconcile the fixture's evidence into the durable station result.
    world.executor.completed.set()
    await _wait_for_state(world, command_id, CommandState.COMPLETED)
    assert not owner.done()
    async with world.client() as reopened:
        snapshot = (await reopened.get(f"{_PREFIX}/snapshot", headers=_HEADERS)).json()
    assert snapshot["commands"][0]["command_id"] == command_id
    assert snapshot["commands"][0]["state"] == "completed"
    assert snapshot["commands"][0]["result_ref"] == f"fixture-result:{command_id}"
    assert len(world.executor.dispatches) == 1
    assert world.app.state.mars_station_task is owner


def test_private_validation_wrapper_preserves_station_openapi_schema():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    operation = schema["paths"][f"{_PREFIX}/agents/{{agent_id}}/commands"]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"] == "#/components/schemas/StationCommand"
    command = schema["components"]["schemas"]["StationCommand"]
    assert set(command["required"]) == {"request_id", "draft"}
    assert command["properties"]["draft"]["maxLength"] == 4000
    assert operation["x-jarvis-dangerous"] is True
