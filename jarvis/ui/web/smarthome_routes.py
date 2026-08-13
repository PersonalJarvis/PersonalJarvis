"""REST API for the Smart Home section.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/smarthome/overview          → providers + devices + rooms, one call
    GET    /api/smarthome/devices           → the unified device list
    GET    /api/smarthome/devices/{id}      → one device; 404 if unknown
    POST   /api/smarthome/devices/{id}/command
                                            → run one command; 200 with ok=false
                                              when the platform refused
    GET    /api/smarthome/rooms             → the user's rooms, with live devices
    POST   /api/smarthome/rooms             → create a room
    PATCH  /api/smarthome/rooms/{id}        → rename, recolour, re-icon, set floor
    DELETE /api/smarthome/rooms/{id}        → delete a room (devices survive)
    POST   /api/smarthome/rooms/reorder     → apply a new display order
    POST   /api/smarthome/rooms/import      → adopt provider areas as rooms
    POST   /api/smarthome/rooms/{id}/devices        → move devices into a room
    DELETE /api/smarthome/rooms/{id}/devices/{did}  → take one device out
    POST   /api/smarthome/rooms/{id}/favorites      → toggle a room favourite
    POST   /api/smarthome/rooms/{id}/command        → one verb for a whole room
    GET    /api/smarthome/providers         → connection health per platform
    GET    /api/smarthome/ecosystems        → the reachability map (static)
    POST   /api/smarthome/demo              → switch the simulated house on/off

Three design points worth knowing before changing anything here:

**Rooms belong to the user, hardware belongs to the platforms.** The room
endpoints write to :mod:`jarvis.smarthome.home_layout` and NEVER back to a hub —
renaming a room here cannot corrupt someone's Home Assistant configuration. The
first read adopts whatever areas the platforms report so a connected hub lands
on a furnished screen; after that, a newly appeared area is offered through
``suggestions`` and never adopted silently, or a deleted room would come back.

**A refused command is not an HTTP error.** "The blind has no colour" and "the
hub is switched off" are answers, not faults, and they arrive as ``200`` with
``ok=false`` plus a plain sentence. Only a malformed request (unknown command
verb, bad body) is a ``4xx``. Turning a switched-off hub into a 502 would make
the section's error state indistinguishable from the server being broken.

**The overview is cached for a few seconds.** Every read here fans out to a hub
on someone's home network — often a Raspberry Pi. The section polls while it is
open, and two open windows would otherwise double that load. The cache is
invalidated the moment a command is executed, so the UI never shows a lamp it
just switched as still off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.smarthome.ecosystems import ecosystems_json
from jarvis.smarthome.home_layout import (
    ROOM_COLORS,
    ROOM_ICONS,
    get_store,
    resolve,
    suggestions,
)
from jarvis.smarthome.models import (
    CONSEQUENTIAL_COMMANDS,
    Command,
    CommandName,
    Device,
    Room,
)
from jarvis.smarthome.registry import SmartHomeRegistry, build_overview, gather_home

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/smarthome", tags=["smarthome"])

# One registry per process. Providers hold pooled HTTP clients (keep-alive to
# the hub), so rebuilding per request would reopen a TLS connection to the home
# network on every poll — the exact cost the pool exists to avoid.
_REGISTRY: SmartHomeRegistry | None = None
_REGISTRY_LOCK = asyncio.Lock()

_CACHE: dict[str, Any] | None = None
_CACHE_AT: float = 0.0


def _cache_ttl() -> float:
    try:
        from jarvis.core.config import load_config

        return max(0.0, float(load_config().smarthome.cache_ttl_seconds))
    except Exception:  # noqa: BLE001
        return 5.0


async def get_registry() -> SmartHomeRegistry:
    global _REGISTRY
    async with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = SmartHomeRegistry()
        return _REGISTRY


def reset_registry() -> None:
    """Drop the cached registry and overview.

    Called when the set of providers changes (the demo toggle) and by tests, so
    a switch flipped in the UI takes effect on the next read rather than after
    a restart.
    """
    global _REGISTRY, _CACHE, _CACHE_AT
    _REGISTRY = None
    _CACHE = None
    _CACHE_AT = 0.0


def invalidate_cache() -> None:
    global _CACHE, _CACHE_AT
    _CACHE = None
    _CACHE_AT = 0.0


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------


class CommandRequest(BaseModel):
    """One verb plus its arguments.

    ``command`` is validated against :class:`CommandName` by hand rather than
    typed as the enum, so an unknown verb produces a sentence naming the valid
    ones instead of Pydantic's schema dump — this endpoint is also the one the
    assistant tool calls, and a readable refusal is what lets it recover.
    """

    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    #: Explicit acknowledgement for a command the person may not be able to undo
    #: from where they are standing — today ``unlock`` and ``open``
    #: (:data:`CONSEQUENTIAL_COMMANDS`). Gated per PAYLOAD rather than by marking
    #: the whole route ``x-jarvis-dangerous``: that flag is per-route, so it
    #: would demand an explicit ``--yes`` to switch a lamp off and teach every
    #: caller to pass it by reflex — which is exactly how the one command that
    #: matters stops being noticed. The desktop UI answers this with a separate,
    #: deliberate Unlock button; a loosely-worded voice command cannot.
    confirm: bool = False


class DemoRequest(BaseModel):
    enabled: bool


class RoomCreateRequest(BaseModel):
    """A new room. Only the name is required — the rest has sensible defaults.

    ``icon`` and ``color`` are plain strings validated by the store rather than
    enums: the frontend renders an unknown value generically, so extending the
    vocabulary stays a backend-only change (the enum-drift rule, AGENTS.md §5).
    """

    name: str
    icon: str = "room"
    color: str = "slate"
    floor: str | None = None
    #: Provider areas this room adopts straight away, so "create from the hub's
    #: kitchen" is one request rather than create-then-assign.
    provider_rooms: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)


class RoomUpdateRequest(BaseModel):
    """A partial update. Unset fields are left ALONE, not cleared.

    ``exclude_unset`` at the call site is what makes that true — sending only
    ``{"name": "Kitchen"}`` must not wipe the room's colour, its adopted areas or
    its favourites (the restore trap, AGENTS.md §7).
    """

    name: str | None = None
    icon: str | None = None
    color: str | None = None
    #: An empty string clears the storey; omitting the key leaves it untouched.
    floor: str | None = None
    provider_rooms: list[str] | None = None
    devices: list[str] | None = None
    excluded_devices: list[str] | None = None
    favorites: list[str] | None = None
    temperature_device: str | None = None
    humidity_device: str | None = None


class RoomReorderRequest(BaseModel):
    room_ids: list[str]


class RoomDevicesRequest(BaseModel):
    device_ids: list[str]


class RoomFavoriteRequest(BaseModel):
    device_id: str


class RoomCommandRequest(BaseModel):
    """One verb for every device in a room that can honour it."""

    command: str


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------


@router.get("/overview", summary="Devices, rooms and connection health in one call")
async def get_overview(refresh: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_AT
    ttl = _cache_ttl()
    now = time.monotonic()
    if not refresh and _CACHE is not None and (now - _CACHE_AT) < ttl:
        return {**_CACHE, "cached": True}
    registry = await get_registry()
    # One fan-out, two consumers: the raw house AND the user's room layer are
    # built from the same answer rather than asking the hub twice.
    statuses, devices, provider_rooms = await gather_home(registry)
    payload = build_overview(statuses, devices, provider_rooms)
    # `rooms` from the registry are the PLATFORMS' areas; the section shows the
    # user's own rooms, so the layout's view replaces them here.
    payload.update(await _layout_payload(registry, devices=devices, provider_rooms=provider_rooms))
    _CACHE = payload
    _CACHE_AT = now
    return {**payload, "cached": False}


@router.get("/devices", summary="Every device across every connected platform")
async def list_devices(room: str | None = None, kind: str | None = None) -> dict[str, Any]:
    registry = await get_registry()
    devices = await registry.devices()
    if room:
        devices = [d for d in devices if d.room == room or d.room_id == room]
    if kind:
        devices = [d for d in devices if str(d.kind) == kind]
    return {"devices": [d.to_json() for d in devices], "total": len(devices)}


@router.get("/devices/{device_id:path}", summary="One device's current state")
async def get_device(device_id: str) -> dict[str, Any]:
    registry = await get_registry()
    device = await registry.device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no device {device_id!r}")
    return device.to_json()


async def _layout_payload(
    registry: SmartHomeRegistry,
    *,
    devices: list[Device] | None = None,
    provider_rooms: list[Room] | None = None,
) -> dict[str, Any]:
    """The room layer, resolved against whatever the platforms report right now.

    Adoption happens ONCE, on the very first read of a machine that has never
    had a layout. Re-adopting on every read would undo every rename and revive
    every deleted room — the restore-trap bug class (AGENTS.md §7).

    ``devices``/``provider_rooms`` let a caller that has ALREADY fanned out to
    every platform hand its answer in, so the overview stays one round trip to
    the home network instead of two.
    """
    store = get_store()
    if devices is None or provider_rooms is None:
        areas, things = await asyncio.gather(registry.rooms(), registry.devices())
    else:
        areas, things = provider_rooms, devices
    if not store.exists() and areas:
        layout = store.adopt(areas)
    else:
        layout = store.load()
    resolved, unassigned = resolve(layout, things)
    return {
        "rooms": [room.to_json() for room in resolved],
        "unassigned": [device.id for device in unassigned],
        # Areas the hub has that no room adopted yet. Offered, never taken.
        "suggestions": [
            {"id": r.id, "name": r.name, "provider": r.provider, "device_count": r.device_count}
            for r in suggestions(layout, areas)
        ],
        # The editor's vocabulary travels WITH the data so a new icon or colour
        # is a backend-only change; a hardcoded copy in the frontend is a copy
        # that drifts.
        "icons": list(ROOM_ICONS),
        "colors": list(ROOM_COLORS),
    }


@router.get("/rooms", summary="The user's rooms, with their live devices attached")
async def list_rooms() -> dict[str, Any]:
    registry = await get_registry()
    return await _layout_payload(registry)


@router.post("/rooms", summary="Create a room", status_code=201)
async def create_room(body: RoomCreateRequest) -> dict[str, Any]:
    try:
        get_store().create_room(
            name=body.name,
            icon=body.icon,
            color=body.color,
            floor=body.floor,
            provider_rooms=body.provider_rooms,
            devices=body.devices,
        )
    except ValueError as exc:
        # A duplicate name or an empty one is the user's mistake, not a fault —
        # 400 with the sentence they should read, never a schema dump.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    invalidate_cache()
    return await _layout_payload(await get_registry())


@router.patch("/rooms/{room_id}", summary="Rename, recolour or re-icon a room")
async def update_room(room_id: str, body: RoomUpdateRequest) -> dict[str, Any]:
    try:
        updated = get_store().update_room(room_id, **body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=f"no room {room_id!r}")
    invalidate_cache()
    return await _layout_payload(await get_registry())


@router.delete("/rooms/{room_id}", summary="Delete a room; its devices survive")
async def delete_room(room_id: str) -> dict[str, Any]:
    if not get_store().delete_room(room_id):
        raise HTTPException(status_code=404, detail=f"no room {room_id!r}")
    invalidate_cache()
    return await _layout_payload(await get_registry())


@router.post("/rooms/reorder", summary="Apply a new display order")
async def reorder_rooms(body: RoomReorderRequest) -> dict[str, Any]:
    get_store().reorder(body.room_ids)
    invalidate_cache()
    return await _layout_payload(await get_registry())


@router.post("/rooms/import", summary="Adopt the platforms' areas as rooms")
async def import_rooms() -> dict[str, Any]:
    """Take over every provider area no room has adopted yet.

    The explicit counterpart to the one-off first-run adoption: a hub that grew
    a new area offers it in ``suggestions``, and this is the button that accepts.
    """
    registry = await get_registry()
    get_store().adopt(await registry.rooms())
    invalidate_cache()
    return await _layout_payload(registry)


@router.post("/rooms/{room_id}/devices", summary="Move devices into a room")
async def assign_devices(room_id: str, body: RoomDevicesRequest) -> dict[str, Any]:
    if get_store().assign_devices(room_id, body.device_ids) is None:
        raise HTTPException(status_code=404, detail=f"no room {room_id!r}")
    invalidate_cache()
    return await _layout_payload(await get_registry())


@router.delete("/rooms/{room_id}/devices/{device_id:path}", summary="Take a device out")
async def remove_device(room_id: str, device_id: str) -> dict[str, Any]:
    if get_store().remove_device(room_id, device_id) is None:
        raise HTTPException(status_code=404, detail=f"no room {room_id!r}")
    invalidate_cache()
    return await _layout_payload(await get_registry())


@router.post("/rooms/{room_id}/favorites", summary="Toggle a room favourite")
async def toggle_favorite(room_id: str, body: RoomFavoriteRequest) -> dict[str, Any]:
    if get_store().toggle_favorite(room_id, body.device_id) is None:
        raise HTTPException(status_code=404, detail=f"no room {room_id!r}")
    invalidate_cache()
    return await _layout_payload(await get_registry())


@router.post("/rooms/{room_id}/command", summary="Run one verb across a whole room")
async def run_room_command(room_id: str, body: RoomCommandRequest) -> dict[str, Any]:
    """ "Everything off in here" — the most-wanted action, done server-side.

    Two rules are load-bearing:

    * **Only reversible verbs.** A room-wide ``unlock`` would open every door in
      the house from one tap, so :data:`CONSEQUENTIAL_COMMANDS` is refused here
      outright rather than gated behind a confirmation. A door is unlocked one
      door at a time, deliberately, on its own card.
    * **Sequential, not concurrent.** A hub on a home network answers a burst of
      a dozen simultaneous service calls by dropping some of them, and a room
      where two lamps stayed on is worse than one that took a second longer.
    """
    try:
        command_name = CommandName(body.command)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown command {body.command!r}") from None
    if command_name in CONSEQUENTIAL_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{command_name!s} cannot be run across a whole room — it "
                "physically opens something and belongs on one device at a time."
            ),
        )
    registry = await get_registry()
    layout = get_store().load()
    devices = await registry.devices()
    room = next((r for r in resolve(layout, devices)[0] if r.layout.id == room_id), None)
    if room is None:
        raise HTTPException(status_code=404, detail=f"no room {room_id!r}")

    changed: list[Device] = []
    failed = 0
    for device in room.devices:
        if not device.supports(command_name) or not device.reachable:
            continue
        result = await registry.execute(device.id, Command(name=command_name))
        if result.ok:
            changed.extend(result.changed)
        else:
            failed += 1
    invalidate_cache()
    return {
        "ok": failed == 0,
        "room_id": room_id,
        "command": str(command_name),
        "changed": [d.to_json() for d in changed],
        "failed": failed,
    }


@router.get("/providers", summary="Connection health per smart-home platform")
async def list_providers() -> dict[str, Any]:
    registry = await get_registry()
    statuses = await registry.statuses()
    return {"providers": [s.to_json() for s in statuses]}


@router.get("/ecosystems", summary="Which smart-home ecosystems are reachable, and how")
async def list_ecosystems() -> dict[str, Any]:
    """The static reachability map — no network calls, safe to call any time."""
    return {"ecosystems": ecosystems_json()}


# ----------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------


@router.post("/devices/{device_id:path}/command", summary="Run one command on a device")
async def run_command(device_id: str, body: CommandRequest) -> dict[str, Any]:
    try:
        command_name = CommandName(body.command)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown command {body.command!r}; valid commands are "
                + ", ".join(str(c) for c in CommandName)
            ),
        ) from None
    if command_name in CONSEQUENTIAL_COMMANDS and not body.confirm:
        # Not an HTTP error: the caller asked a legitimate question and the
        # answer is "say so explicitly". `requires_confirmation` lets a UI put
        # a confirm step in front of it, and lets the assistant ask out loud
        # rather than guessing.
        return {
            "ok": False,
            "device_id": device_id,
            "command": str(command_name),
            "changed": [],
            "requires_confirmation": True,
            "error": (
                f"{command_name!s} physically opens something and cannot be "
                "undone remotely — repeat the request with confirm set."
            ),
        }
    registry = await get_registry()
    result = await registry.execute(device_id, Command(name=command_name, args=body.args))
    # The device just moved; the next poll must not serve the state from before.
    invalidate_cache()
    return result.to_json()


@router.post("/demo", summary="Switch the simulated demo home on or off")
def set_demo(body: DemoRequest) -> dict[str, Any]:
    """Persist ``[smarthome] demo_mode`` and rebuild the provider set.

    Sync ``def`` on purpose: the TOML write is blocking and FastAPI runs sync
    endpoints in its threadpool, which keeps the event loop free rather than
    stalling every other request behind a disk write.
    """
    from jarvis.core.config_writer import set_smarthome_setting

    try:
        set_smarthome_setting("demo_mode", body.enabled)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reset_registry()
    return {"demo_mode": body.enabled}
