"""The assistant's door into the section.

The voice layer is where a smart-home mistake becomes expensive, so the pinned
properties are all about NOT guessing:

* a device is found by the name a person would say, not by an entity id;
* an ambiguous name comes back as a question with candidates, never a pick;
* a command the device cannot honour is refused with a readable reason;
* `unlock` and `open` are refused until the user has explicitly agreed;
* nothing the platform said in an error body reaches the transcript.
"""

from __future__ import annotations

import pytest

from jarvis.plugins.tool.smart_home import SmartHomeTool
from jarvis.smarthome.models import (
    Capability,
    Command,
    CommandResult,
    Device,
    DeviceKind,
    Room,
)
from jarvis.smarthome.provider import ConnectionState, ProviderStatus
from jarvis.smarthome.providers.demo import DemoProvider
from jarvis.smarthome.registry import SmartHomeRegistry


def _tool(*providers: object) -> SmartHomeTool:
    return SmartHomeTool(registry=SmartHomeRegistry(list(providers or (DemoProvider(),))))  # type: ignore[arg-type]


class TwinLamps:
    """Two devices whose names both match "lamp" — the ambiguity case."""

    id = "twin"
    display_name = "Twin"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.id, display_name=self.display_name, state=ConnectionState.CONNECTED
        )

    async def devices(self) -> list[Device]:
        return [
            Device(
                id=f"twin:light.{slug}",
                provider=self.id,
                native_id=f"light.{slug}",
                name=name,
                kind=DeviceKind.LIGHT,
                capabilities=(Capability.ON_OFF,),
                state={"on_off": False},
                room=room,
            )
            for slug, name, room in (
                ("a", "Desk lamp", "Study"),
                ("b", "Floor lamp", "Study"),
            )
        ]

    async def rooms(self) -> list[Room]:
        return [Room(id="twin:Study", name="Study", provider=self.id)]

    async def execute(self, native_id: str, command: Command) -> CommandResult:
        return CommandResult(ok=True, device_id=f"{self.id}:{native_id}", command=command.name)


@pytest.mark.asyncio
async def test_finds_a_device_by_the_name_a_person_says() -> None:
    out = await _tool().get_device(device="kitchen spots")
    assert out["name"] == "Kitchen spots"
    assert out["room"] == "Kitchen"


@pytest.mark.asyncio
async def test_a_room_plus_thing_phrase_resolves() -> None:
    out = await _tool().get_device(device="bedroom blinds")
    assert out["name"] == "Bedroom blinds"


@pytest.mark.asyncio
async def test_a_near_miss_still_resolves() -> None:
    """Speech recognition drops plurals; "kitchen spot" must still land."""
    out = await _tool().get_device(device="Kitchen spot")
    assert out["name"] == "Kitchen spots"


@pytest.mark.asyncio
async def test_an_ambiguous_name_asks_instead_of_picking() -> None:
    out = await _tool(TwinLamps()).command(device="lamp", command="turn_on")
    assert "several devices" in out["error"]
    assert {c["name"] for c in out["candidates"]} == {"Desk lamp", "Floor lamp"}


@pytest.mark.asyncio
async def test_an_unknown_name_says_so_rather_than_guessing() -> None:
    out = await _tool().get_device(device="jacuzzi")
    assert "No device called" in out["error"]


@pytest.mark.asyncio
async def test_a_command_reports_what_actually_changed() -> None:
    out = await _tool().command(
        device="Kitchen spots", command="set_brightness", args={"brightness": 30}
    )
    assert out["changed"][0]["state"]["brightness"] == 30
    assert out["changed"][0]["state"]["on_off"] is True


@pytest.mark.asyncio
async def test_a_command_the_device_cannot_honour_is_refused_readably() -> None:
    out = await _tool().command(device="Bedroom blinds", command="set_color")
    assert "cannot do that" in out["error"]


@pytest.mark.asyncio
async def test_unlocking_requires_the_user_to_have_agreed() -> None:
    tool = _tool()
    blocked = await tool.command(device="Front door", command="unlock")
    assert blocked["requires_confirmation"] is True
    assert (await tool.get_device(device="Front door"))["state"]["locked"] is True

    allowed = await tool.command(device="Front door", command="unlock", confirm=True)
    assert allowed["changed"][0]["state"]["locked"] is False


@pytest.mark.asyncio
async def test_locking_is_never_gated() -> None:
    out = await _tool().command(device="Front door", command="lock")
    assert "error" not in out


@pytest.mark.asyncio
async def test_list_devices_reports_what_each_device_can_do() -> None:
    out = await _tool().list_devices(kind="light")
    assert out["total"] > 0
    assert {d["kind"] for d in out["devices"]} == {"light"}
    assert "set_brightness" in out["devices"][0]["can"]


@pytest.mark.asyncio
async def test_list_devices_filters_by_room() -> None:
    out = await _tool().list_devices(room="Kitchen")
    assert {d["room"] for d in out["devices"]} == {"Kitchen"}


@pytest.mark.asyncio
async def test_no_platform_connected_says_where_to_go() -> None:
    tool = SmartHomeTool(registry=SmartHomeRegistry([]))
    out = await tool.list_devices()
    assert "Smart Home section" in out["error"]


@pytest.mark.asyncio
async def test_an_unknown_verb_names_the_valid_ones() -> None:
    out = await _tool().command(device="Kitchen spots", command="levitate")
    assert "turn_on" in out["error"]


def test_the_tool_is_gated_and_never_a_spawn() -> None:
    """risk_tier "ask": a command here physically changes the user's home."""
    assert SmartHomeTool.risk_tier == "ask"
    assert "spawn" not in SmartHomeTool.description.lower()
