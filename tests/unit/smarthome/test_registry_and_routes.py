"""Aggregation across platforms, and the REST surface the section reads.

The registry's job is to make several ecosystems look like one house, so the
properties pinned here are the ones that decide whether that illusion holds:

* a platform that is switched off must not blank the devices from the others;
* a command is routed by the id's provider prefix, never by guesswork;
* a command a device cannot honour is refused BEFORE it hits the network.

For the routes, the load-bearing rule is that a refused command is an ANSWER
(200 with ok=false), not an HTTP error — a hub being off must not look like the
server being broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.smarthome.home_layout import HomeLayoutStore, reset_store
from jarvis.smarthome.models import (
    Capability,
    Command,
    CommandName,
    CommandResult,
    Device,
    DeviceKind,
    Room,
)
from jarvis.smarthome.provider import ConnectionState, ProviderStatus
from jarvis.smarthome.providers.demo import DemoProvider
from jarvis.smarthome.registry import SmartHomeRegistry, overview
from jarvis.ui.web import smarthome_routes


class BrokenProvider:
    """A platform that fails every call — the switched-off hub."""

    id = "broken"
    display_name = "Broken hub"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.id,
            display_name=self.display_name,
            state=ConnectionState.UNREACHABLE,
        )

    async def devices(self) -> list[Device]:
        raise RuntimeError("hub is off")

    async def rooms(self) -> list[Room]:
        raise RuntimeError("hub is off")

    async def execute(self, native_id: str, command: Command) -> CommandResult:
        raise RuntimeError("hub is off")


class SecondHome:
    """A second platform whose native ids deliberately COLLIDE with the demo's."""

    id = "second"
    display_name = "Second home"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.id,
            display_name=self.display_name,
            state=ConnectionState.CONNECTED,
        )

    async def devices(self) -> list[Device]:
        return [
            Device(
                id="second:light.kitchen",
                provider=self.id,
                native_id="light.kitchen",
                name="Other kitchen",
                kind=DeviceKind.LIGHT,
                capabilities=(Capability.ON_OFF,),
                state={"on_off": False},
                room="Kitchen",
            )
        ]

    async def rooms(self) -> list[Room]:
        return [Room(id="second:Kitchen", name="Kitchen", provider=self.id)]

    async def execute(self, native_id: str, command: Command) -> CommandResult:
        return CommandResult(ok=True, device_id=f"{self.id}:{native_id}", command=command.name)


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_broken_platform_does_not_blank_the_house() -> None:
    registry = SmartHomeRegistry([DemoProvider(), BrokenProvider()])
    devices = await registry.devices()
    assert devices, "a switched-off hub must not take the working one down with it"
    assert all(d.provider == "demo" for d in devices)


@pytest.mark.asyncio
async def test_broken_platform_still_reports_a_status() -> None:
    """Silence would look like "connected"; the card has to say otherwise."""
    statuses = await SmartHomeRegistry([BrokenProvider()]).statuses()
    assert [s.state for s in statuses] == [ConnectionState.UNREACHABLE]


@pytest.mark.asyncio
async def test_colliding_native_ids_route_to_the_right_platform() -> None:
    registry = SmartHomeRegistry([DemoProvider(), SecondHome()])
    devices = await registry.devices()
    kitchens = [d for d in devices if d.native_id == "light.kitchen"]
    assert {d.provider for d in kitchens} == {"demo", "second"}

    result = await registry.execute("second:light.kitchen", Command(name=CommandName.TURN_ON))
    assert result.ok is True
    assert result.device_id == "second:light.kitchen"


@pytest.mark.asyncio
async def test_unsupported_command_is_refused_before_the_network() -> None:
    registry = SmartHomeRegistry([DemoProvider()])
    result = await registry.execute("demo:cover.bedroom", Command(name=CommandName.SET_COLOR))
    assert result.ok is False
    assert "cannot do that" in (result.error or "")


@pytest.mark.asyncio
async def test_unknown_provider_prefix_is_refused_by_name() -> None:
    registry = SmartHomeRegistry([DemoProvider()])
    result = await registry.execute("nope:light.x", Command(name=CommandName.TURN_ON))
    assert result.ok is False
    assert "nope" in (result.error or "")


@pytest.mark.asyncio
async def test_overview_counts_devices_per_platform() -> None:
    payload = await overview(SmartHomeRegistry([DemoProvider()]))
    assert payload["connected"] is True
    assert payload["providers"][0]["device_count"] == len(payload["devices"])
    assert "turn_on" in payload["commands"]


@pytest.mark.asyncio
async def test_demo_scene_actually_moves_the_house() -> None:
    """A scene that changes nothing would teach the wrong idea of a scene."""
    registry = SmartHomeRegistry([DemoProvider()])
    await registry.execute("demo:light.living_room", Command(name=CommandName.TURN_ON))
    await registry.execute("demo:scene.good_night", Command(name=CommandName.ACTIVATE))
    devices = {d.native_id: d for d in await registry.devices()}
    assert devices["light.living_room"].state["on_off"] is False
    assert devices["lock.front_door"].state["locked"] is True


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """A server whose only platform is the deterministic demo home.

    The room layout is redirected into ``tmp_path``: the section now WRITES,
    and a test that persisted into the real app-data directory would rearrange
    the developer's own house.
    """
    smarthome_routes.reset_registry()
    reset_store(HomeLayoutStore(path=tmp_path / "home.json"))
    monkeypatch.setattr(
        smarthome_routes, "SmartHomeRegistry", lambda: SmartHomeRegistry([DemoProvider()])
    )
    app = FastAPI()
    app.include_router(smarthome_routes.router)
    with TestClient(app) as test_client:
        yield test_client
    smarthome_routes.reset_registry()
    reset_store(None)


def test_overview_answers_devices_rooms_and_providers_at_once(client: TestClient) -> None:
    body = client.get("/api/smarthome/overview").json()
    assert body["connected"] is True
    assert body["devices"] and body["rooms"] and body["providers"]
    assert body["cached"] is False


def test_second_overview_is_served_from_cache(client: TestClient) -> None:
    """The hub is on a home network; the section must not re-ask every second."""
    client.get("/api/smarthome/overview")
    assert client.get("/api/smarthome/overview").json()["cached"] is True
    assert client.get("/api/smarthome/overview?refresh=true").json()["cached"] is False


def test_a_command_invalidates_the_cache(client: TestClient) -> None:
    """A lamp just switched must never be served from the pre-command snapshot."""
    client.get("/api/smarthome/overview")
    client.post("/api/smarthome/devices/demo:light.kitchen/command", json={"command": "turn_on"})
    assert client.get("/api/smarthome/overview").json()["cached"] is False


def test_command_runs_and_reports_what_changed(client: TestClient) -> None:
    res = client.post(
        "/api/smarthome/devices/demo:light.kitchen/command",
        json={"command": "set_brightness", "args": {"brightness": 30}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["changed"][0]["state"]["brightness"] == 30
    assert body["changed"][0]["state"]["on_off"] is True


def test_a_refused_command_is_an_answer_not_an_http_error(client: TestClient) -> None:
    res = client.post(
        "/api/smarthome/devices/demo:cover.bedroom/command",
        json={"command": "set_color", "args": {}},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert res.json()["error"]


def test_unlocking_needs_an_explicit_acknowledgement(client: TestClient) -> None:
    """A loosely-worded request must never open a door as a side effect."""
    first = client.post(
        "/api/smarthome/devices/demo:lock.front_door/command",
        json={"command": "unlock"},
    ).json()
    assert first["ok"] is False
    assert first["requires_confirmation"] is True

    still_locked = client.get("/api/smarthome/devices/demo:lock.front_door").json()
    assert still_locked["state"]["locked"] is True

    second = client.post(
        "/api/smarthome/devices/demo:lock.front_door/command",
        json={"command": "unlock", "confirm": True},
    ).json()
    assert second["ok"] is True
    assert second["changed"][0]["state"]["locked"] is False


def test_locking_needs_no_acknowledgement(client: TestClient) -> None:
    """Only the irreversible direction is gated; making a home safer is not."""
    body = client.post(
        "/api/smarthome/devices/demo:lock.front_door/command", json={"command": "lock"}
    ).json()
    assert body["ok"] is True


def test_switching_a_light_needs_no_acknowledgement(client: TestClient) -> None:
    """Gating everything would teach callers to pass confirm by reflex."""
    body = client.post(
        "/api/smarthome/devices/demo:light.kitchen/command", json={"command": "turn_on"}
    ).json()
    assert body["ok"] is True
    assert "requires_confirmation" not in body


def test_an_unknown_verb_is_a_400_that_names_the_valid_ones(client: TestClient) -> None:
    res = client.post(
        "/api/smarthome/devices/demo:light.kitchen/command", json={"command": "explode"}
    )
    assert res.status_code == 400
    assert "turn_on" in res.json()["detail"]


def test_device_lookup_uses_the_namespaced_id(client: TestClient) -> None:
    ok = client.get("/api/smarthome/devices/demo:light.kitchen")
    assert ok.status_code == 200
    assert ok.json()["name"] == "Kitchen spots"
    assert client.get("/api/smarthome/devices/demo:light.nope").status_code == 404


def test_devices_can_be_filtered_by_room_and_kind(client: TestClient) -> None:
    kitchen = client.get("/api/smarthome/devices", params={"room": "Kitchen"}).json()
    assert kitchen["total"] > 0
    assert {d["room"] for d in kitchen["devices"]} == {"Kitchen"}

    lights = client.get("/api/smarthome/devices", params={"kind": "light"}).json()
    assert {d["kind"] for d in lights["devices"]} == {"light"}


def test_ecosystem_map_is_served_without_touching_the_network(client: TestClient) -> None:
    body = client.get("/api/smarthome/ecosystems").json()
    ids = {eco["id"] for eco in body["ecosystems"]}
    assert {"home_assistant", "matter", "google_home", "amazon_alexa"} <= ids
