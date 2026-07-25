"""Home Assistant tool — driven against a mock transport, no credentials.

Two things make this plugin different from the other REST-backed ones and both
are covered here: the server address is per-user state rather than a constant,
and a call physically changes something in the user's home.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jarvis.plugins.tool.home_assistant_rest import HomeAssistantRestTool

BASE = "http://ha.test:8123"

STATES = [
    {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {
            "friendly_name": "Living room",
            "brightness": 180,
            "supported_color_modes": ["hs"],
            "icon": "mdi:lamp",
        },
    },
    {
        "entity_id": "cover.garage_door",
        "state": "open",
        "attributes": {"friendly_name": "Garage door", "device_class": "garage"},
    },
    {
        "entity_id": "climate.bedroom",
        "state": "heat",
        "attributes": {"friendly_name": "Bedroom", "current_temperature": 19.5},
    },
]


def _tool(
    handler, base: str | None = BASE, token: str | None = "llat"  # noqa: S107 - fake token
) -> HomeAssistantRestTool:
    return HomeAssistantRestTool(
        connection_provider=lambda: (base, token),
        transport=httpx.MockTransport(handler),
    )


def _states_handler(seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/api/states":
            return httpx.Response(200, json=STATES)
        if path.startswith("/api/states/"):
            entity = path.rsplit("/", 1)[-1]
            for state in STATES:
                if state["entity_id"] == entity:
                    return httpx.Response(200, json=state)
            return httpx.Response(404, json={"message": "Entity not found."})
        if path.startswith("/api/services/"):
            return httpx.Response(
                200,
                json=[
                    {
                        **STATES[0],
                        "state": "off",
                        "attributes": {"friendly_name": "Living room"},
                    }
                ],
            )
        return httpx.Response(500)

    return handler


@pytest.mark.asyncio
async def test_calls_the_users_own_server_not_a_fixed_endpoint() -> None:
    """The address is user data. Getting this wrong would send a home-network
    token to whatever host the catalog happened to name."""
    seen: list[httpx.Request] = []
    result = await _tool(_states_handler(seen)).list_entities()

    assert result["total"] == 3
    assert str(seen[0].url).startswith(BASE)
    assert seen[0].headers["Authorization"] == "Bearer llat"


@pytest.mark.asyncio
async def test_summarizes_entities_instead_of_dumping_them() -> None:
    """A real home has hundreds of entities carrying dozens of fields each.
    Passing that through would bury the answer and blow the prompt budget."""
    result = await _tool(_states_handler()).list_entities()

    first = result["entities"][0]
    assert first["entity_id"] == "light.living_room"
    assert first["friendly_name"] == "Living room"
    assert first["brightness"] == 180
    assert "supported_color_modes" not in first
    assert "icon" not in first


@pytest.mark.asyncio
async def test_filters_by_domain() -> None:
    result = await _tool(_states_handler()).list_entities(domain_filter="light")
    assert [e["entity_id"] for e in result["entities"]] == ["light.living_room"]


@pytest.mark.asyncio
async def test_reads_one_entity() -> None:
    result = await _tool(_states_handler()).get_state(entity_id="climate.bedroom")
    assert result["state"] == "heat"
    assert result["current_temperature"] == 19.5


@pytest.mark.asyncio
async def test_a_missing_entity_says_so_rather_than_raising() -> None:
    result = await _tool(_states_handler()).get_state(entity_id="light.nope")
    assert "no entity" in result["error"]


@pytest.mark.asyncio
async def test_a_service_call_reports_what_actually_changed() -> None:
    """Home Assistant answers with the entities whose state changed. Reading
    that back is what lets the assistant say what happened instead of 'done'."""
    seen: list[httpx.Request] = []
    result = await _tool(_states_handler(seen)).call_service(
        domain="light", service="turn_off", entity_id="light.living_room"
    )

    assert result["called"] == "light.turn_off"
    assert result["changed"][0]["state"] == "off"
    body = seen[-1].content.decode()
    assert "light.living_room" in body


@pytest.mark.asyncio
async def test_service_data_is_passed_through() -> None:
    seen: list[httpx.Request] = []
    await _tool(_states_handler(seen)).call_service(
        domain="climate",
        service="set_temperature",
        entity_id="climate.bedroom",
        data={"temperature": 21},
    )
    sent = json.loads(seen[-1].content.decode())
    assert sent["temperature"] == 21
    assert sent["entity_id"] == "climate.bedroom"


@pytest.mark.asyncio
async def test_acting_on_a_home_is_gated_rather_than_silent() -> None:
    """Read-only plugins sit at 'monitor'. Turning off the wrong light is
    recoverable, unlocking a door is not, so this one lets the safety layer
    weigh in."""
    assert HomeAssistantRestTool.risk_tier == "ask"


@pytest.mark.asyncio
async def test_an_unconfigured_plugin_explains_itself() -> None:
    tool = HomeAssistantRestTool(connection_provider=lambda: (None, None))
    result = await tool.list_entities()
    assert "not connected" in result["error"]


@pytest.mark.asyncio
async def test_an_unreachable_home_says_what_to_check() -> None:
    """Home Assistant lives on the home network, so the common failure is a
    server this machine cannot see — not a bad token. 'ConnectError' tells the
    user nothing."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    tool = _tool(refuse)
    result = await tool.execute({"action": "list_entities"}, ctx=None)

    assert result.success is False
    assert "home network" in result.error


@pytest.mark.asyncio
async def test_a_rejected_token_points_at_the_fix() -> None:
    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    result = await _tool(unauthorized).execute({"action": "list_entities"}, ctx=None)

    assert result.success is False
    assert "long-lived token" in result.error
