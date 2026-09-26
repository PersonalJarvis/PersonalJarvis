"""Rover HTTP authority, private validation and durable action replay."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.ui.web.mars_routes import ensure_mars_navigation

from .test_mars_navigation_http import BASE, HEADERS, PREFIX
from .test_mars_navigation_http import world as world

pytestmark = pytest.mark.no_auto_web_auth


@pytest.mark.parametrize("action", ["reserve", "board", "travel", "cancel", "exit"])
@pytest.mark.parametrize(
    "headers,status",
    [({"Origin": BASE}, 401), ({**HEADERS, "Origin": "https://foreign.invalid"}, 403)],
)
async def test_rover_authentication_precedes_owner_creation(world, action, headers, status):
    suffix = "" if action == "reserve" else f"/unknown/{action}"
    payload = {"request_id": "attempt"}
    if action == "reserve":
        payload["vehicle_id"] = "outpost-rover"
    if action == "travel":
        payload["destination_dock_id"] = "outpost-b"
    async with world.client() as client:
        response = await client.post(
            f"{PREFIX}/agents/one/rides{suffix}", headers=headers, json=payload
        )
    assert response.status_code == status
    assert world.calls == []
    assert not world.runtime.store.path.exists()


@pytest.mark.parametrize("action", ["reserve", "board", "travel", "cancel", "exit"])
async def test_rejected_rover_input_never_echoes_private_data(world, action, caplog):
    private = "sk-proj-" + "B7" * 20
    suffix = "" if action == "reserve" else f"/unknown/{action}"
    async with world.client() as client:
        response = await client.post(
            f"{PREFIX}/agents/one/rides{suffix}",
            headers=HEADERS,
            json={"request_id": "attempt", private: private},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"reason": "invalid_rover_request"}}
    assert private not in response.text + caplog.text
    assert world.calls == []


async def test_rover_uses_real_agent_and_idempotent_reservation_without_task_dispatch(world):
    service = await ensure_mars_navigation(world.app.state)
    task = world.app.state.mars_navigation_task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    payload = {"request_id": "reserve-http", "vehicle_id": "outpost-rover"}
    async with world.client() as client:
        rejected = await client.post(
            f"{PREFIX}/agents/fabricated/rides", headers=HEADERS, json=payload
        )
        assert rejected.status_code == 403
        first = await client.post(f"{PREFIX}/agents/one/rides", headers=HEADERS, json=payload)
        assert first.status_code == 200, first.text
        second = await client.post(f"{PREFIX}/agents/one/rides", headers=HEADERS, json=payload)
        assert second.status_code == 200
        assert first.json()["ride_id"] == second.json()["ride_id"]
        ride_id = first.json()["ride_id"]
        early = await client.post(
            f"{PREFIX}/agents/one/rides/{ride_id}/board",
            headers=HEADERS,
            json={"request_id": "too-early"},
        )
        assert early.status_code == 409
        other = await client.post(
            f"{PREFIX}/agents/two/rides/{ride_id}/cancel",
            headers=HEADERS,
            json={"request_id": "wrong-actor"},
        )
        assert other.status_code in (403, 404)
        cancel = await client.post(
            f"{PREFIX}/agents/one/rides/{ride_id}/cancel",
            headers=HEADERS,
            json={"request_id": "cancel"},
        )
        assert cancel.status_code == 200
        assert cancel.json()["state"] == "canceled"
    assert (await service.snapshot()).rides[0].ride_id == ride_id
    assert world.executor.dispatched == []


async def test_canonical_outpost_board_travel_exit_and_retry_through_authenticated_http(world):
    service = await ensure_mars_navigation(world.app.state)
    task = world.app.state.mars_navigation_task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    now = [service.store.clock()]
    service.store.clock = lambda: now[0]
    async with world.client() as client:

        async def action(name, payload, ride_id=None):
            suffix = "" if name == "reserve" else f"/{ride_id}/{name}"
            response = await client.post(
                f"{PREFIX}/agents/one/rides{suffix}", headers=HEADERS, json=payload
            )
            assert response.status_code == 200, response.text
            return response.json()

        async def wait_for(state):
            for _ in range(120):
                now[0] += 500
                await service.advance()
                response = await client.get(f"{PREFIX}/navigation/snapshot", headers=HEADERS)
                assert response.status_code == 200
                snapshot = response.json()
                if snapshot["rides"][0]["state"] == state:
                    return snapshot
            pytest.fail(f"Rover failed to reach {state}")

        ride = await action(
            "reserve", {"request_id": "canonical-reserve", "vehicle_id": "outpost-rover"}
        )
        ride_id = ride["ride_id"]
        ready = await wait_for("ready_to_board")
        assert ready["commands"][0]["current_node"] == "rover-a-board"
        boarded = await action("board", {"request_id": "canonical-board"}, ride_id)
        assert boarded["attached"]
        travel = {"request_id": "canonical-travel", "destination_dock_id": "outpost-b"}
        await action("travel", travel, ride_id)
        arrived = await wait_for("arrived")
        assert arrived["vehicles"][0]["current_node"] == "rover-b-dock"
        assert all(body["actor"]["kind"] == "vehicle" for body in arrived["occupancies"])
        result = await action("exit", {"request_id": "canonical-exit"}, ride_id)
        assert result["state"] == "completed" and not result["attached"]
        replay = await action("travel", travel, ride_id)
        assert replay["state"] == "completed"
        snapshot = (await client.get(f"{PREFIX}/navigation/snapshot", headers=HEADERS)).json()
        assert snapshot["vehicles"][0]["ride_id"] is None
        assert snapshot["commands"][0]["current_node"] == "rover-b-exit"
        assert len(snapshot["rides"]) == 1
    assert world.executor.dispatched == []
