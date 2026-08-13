"""The Home Assistant adapter: normalisation, addressing, and honest failure.

The properties that matter are the ones a user would notice going wrong:

* a dimmable lamp gains a brightness control and a filament bulb does not;
* brightness arrives on 0-100 rather than Home Assistant's 0-255;
* an `unavailable` entity is shown as unreachable, not as "off" — the two mean
  completely different things to somebody deciding whether to drive home;
* a hub that is switched off reads as UNREACHABLE, never as a bad token, so the
  user is not sent to create a credential that was never the problem;
* an error message never carries the provider's own body, which can echo a
  token back at us.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from jarvis.smarthome.models import Command, CommandName
from jarvis.smarthome.provider import ConnectionState
from jarvis.smarthome.providers.home_assistant import (
    HomeAssistantProvider,
    command_to_service,
    entity_to_device,
)

BASE = "http://hub.local:8123"


def _entity(entity_id: str, state: str, **attrs: Any) -> dict[str, Any]:
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


def _provider(handler: Any) -> HomeAssistantProvider:
    return HomeAssistantProvider(
        connection_provider=lambda: (BASE, "tok"),
        transport=httpx.MockTransport(handler),
    )


# ----------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------


def test_dimmable_light_gains_brightness_and_scales_to_percent() -> None:
    device = entity_to_device(
        _entity(
            "light.kitchen",
            "on",
            friendly_name="Kitchen",
            brightness=255,
            supported_color_modes=["brightness"],
        ),
        {"light.kitchen": "Kitchen"},
    )
    assert device is not None
    assert "brightness" in [str(c) for c in device.capabilities]
    # 255 is Home Assistant's maximum; everything above this layer speaks 0-100.
    assert device.state["brightness"] == 100
    assert device.state["on_off"] is True
    assert device.room == "Kitchen"
    assert device.id == "home_assistant:light.kitchen"


def test_non_dimmable_light_has_no_brightness_control() -> None:
    device = entity_to_device(
        _entity("light.hall", "off", supported_color_modes=["onoff"]),
        {},
    )
    assert device is not None
    assert [str(c) for c in device.capabilities] == ["on_off"]


def test_colour_light_gains_colour_and_temperature() -> None:
    device = entity_to_device(
        _entity(
            "light.desk",
            "on",
            supported_color_modes=["hs", "color_temp"],
            hs_color=[30, 60],
            color_temp_kelvin=2700,
        ),
        {},
    )
    assert device is not None
    caps = {str(c) for c in device.capabilities}
    assert {"color", "color_temperature", "brightness"} <= caps
    assert device.state["color"] == {"hue": 30, "saturation": 60}
    assert device.state["color_temperature"] == 2700


def test_unavailable_entity_is_unreachable_not_off() -> None:
    """`unavailable` means the hardware is gone; "off" would be a lie."""
    device = entity_to_device(_entity("light.garage", "unavailable"), {})
    assert device is not None
    assert device.reachable is False


def test_system_entities_are_not_part_of_the_home() -> None:
    for entity_id in ("sun.sun", "update.core", "person.someone", "zone.home"):
        assert entity_to_device(_entity(entity_id, "on"), {}) is None


def test_climate_carries_both_temperatures() -> None:
    device = entity_to_device(
        _entity("climate.living", "heat", temperature=21.0, current_temperature=19.5),
        {},
    )
    assert device is not None
    assert device.state["target_temperature"] == 21.0
    assert device.state["current_temperature"] == 19.5
    assert device.supports(CommandName.SET_TEMPERATURE)


def test_outlet_device_class_changes_the_kind_not_the_capabilities() -> None:
    device = entity_to_device(
        _entity("switch.coffee", "off", device_class="outlet"), {}
    )
    assert device is not None
    assert str(device.kind) == "outlet"
    assert device.supports(CommandName.TURN_ON)


def test_unknown_domain_degrades_to_a_readable_device() -> None:
    """An unmodelled domain must still appear, never vanish from the list."""
    device = entity_to_device(_entity("weather.home", "sunny"), {})
    assert device is not None
    assert str(device.kind) == "other"


# ----------------------------------------------------------------------
# Command mapping
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity_id", "command", "expected"),
    [
        ("light.kitchen", Command(name=CommandName.TURN_OFF), ("light", "turn_off", {})),
        (
            "light.kitchen",
            Command(name=CommandName.SET_BRIGHTNESS, args={"brightness": 30}),
            ("light", "turn_on", {"brightness_pct": 30}),
        ),
        (
            "cover.blind",
            Command(name=CommandName.SET_POSITION, args={"position": 40}),
            ("cover", "set_cover_position", {"position": 40}),
        ),
        ("lock.front", Command(name=CommandName.UNLOCK), ("lock", "unlock", {})),
        (
            "climate.living",
            Command(name=CommandName.SET_TEMPERATURE, args={"temperature": 22}),
            ("climate", "set_temperature", {"temperature": 22}),
        ),
        ("button.doorbell", Command(name=CommandName.ACTIVATE), ("button", "press", {})),
        ("scene.night", Command(name=CommandName.ACTIVATE), ("scene", "turn_on", {})),
    ],
)
def test_command_maps_onto_the_documented_service(
    entity_id: str, command: Command, expected: tuple[str, str, dict[str, Any]]
) -> None:
    assert command_to_service(entity_id, command) == expected


def test_volume_is_rescaled_to_the_provider_unit() -> None:
    """The section speaks 0-100; Home Assistant's media volume is 0.0-1.0."""
    mapped = command_to_service(
        "media_player.living", Command(name=CommandName.SET_VOLUME, args={"volume": 50})
    )
    assert mapped is not None
    assert mapped[2]["volume_level"] == pytest.approx(0.5)


def test_toggle_uses_the_universal_domain() -> None:
    """`homeassistant.toggle` works for entities this adapter does not model."""
    assert command_to_service("light.x", Command(name=CommandName.TOGGLE)) == (
        "homeassistant",
        "toggle",
        {},
    )


# ----------------------------------------------------------------------
# Live behaviour
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devices_reads_states_and_areas_in_one_pass() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.path))
        if request.url.path == "/api/states":
            return httpx.Response(
                200,
                json=[
                    _entity("light.kitchen", "on", supported_color_modes=["brightness"]),
                    _entity("sun.sun", "above_horizon"),
                ],
            )
        if request.url.path == "/api/template":
            return httpx.Response(200, text=json.dumps([{"e": "light.kitchen", "a": "Kitchen"}]))
        return httpx.Response(404)

    devices = await _provider(handler).devices()
    assert [d.native_id for d in devices] == ["light.kitchen"]
    assert devices[0].room == "Kitchen"
    assert seen.count("/api/template") == 1


@pytest.mark.asyncio
async def test_a_hub_without_the_template_api_still_lists_devices() -> None:
    """Rooms are a nicety; the device list is not. A 403 must not blank it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/states":
            return httpx.Response(200, json=[_entity("light.kitchen", "on")])
        return httpx.Response(403, json={"message": "forbidden"})

    devices = await _provider(handler).devices()
    assert len(devices) == 1
    assert devices[0].room is None


@pytest.mark.asyncio
async def test_unreachable_hub_is_a_state_not_a_credential_problem() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    status = await _provider(handler).status()
    assert status.state is ConnectionState.UNREACHABLE
    assert "home network" in (status.detail or "")


@pytest.mark.asyncio
async def test_rejected_token_asks_for_a_new_token() -> None:
    status = await _provider(lambda request: httpx.Response(401)).status()
    assert status.state is ConnectionState.NEEDS_REAUTH
    assert "token" in (status.detail or "").lower()


@pytest.mark.asyncio
async def test_missing_credential_is_not_configured() -> None:
    provider = HomeAssistantProvider(connection_provider=lambda: (None, None))
    status = await provider.status()
    assert status.state is ConnectionState.NOT_CONFIGURED
    assert await provider.devices() == []


@pytest.mark.asyncio
async def test_execute_reads_the_changed_entities_back() -> None:
    """Reporting what changed is what turns "done" into an actual answer."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json=[_entity("light.kitchen", "off", friendly_name="Kitchen")]
        )

    result = await _provider(handler).execute(
        "light.kitchen", Command(name=CommandName.TURN_OFF)
    )
    assert result.ok is True
    assert captured["path"] == "/api/services/light/turn_off"
    assert captured["body"] == {"entity_id": "light.kitchen"}
    assert [d.state["on_off"] for d in result.changed] == [False]


@pytest.mark.asyncio
async def test_failure_message_never_carries_the_provider_body() -> None:
    """A vendor error body can echo a token back; it must not reach the screen."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Bearer super-secret-token rejected")

    result = await _provider(handler).execute(
        "light.kitchen", Command(name=CommandName.TURN_ON)
    )
    assert result.ok is False
    assert "super-secret-token" not in (result.error or "")
