"""A simulated house — the section's honest answer to "I own nothing yet".

Two real jobs, neither of them decoration:

* **A downloader with no hub can still see what the section does.** Open Smart
  Home on a fresh install and the alternative is an empty page with a Connect
  button, which teaches nobody anything. The demo home shows the device cards,
  the room grouping and the controls responding, so the decision to buy a hub is
  made with the thing in front of you.
* **Tests and CI need a provider that cannot flake.** Everything above this
  layer — the routes, the aggregation, the section UI — can be exercised end to
  end without a network.

It is OPT-IN and says so everywhere. A simulated lamp that silently pretends to
be real would be the worst possible bug in a section whose whole promise is
"this is what your home is doing right now", so the provider is off unless
``[smarthome] demo_mode = true``, its display name carries the word Demo, and
its status detail states plainly that nothing here is physical.

State is per-process and in memory on purpose: a demo that survives a restart
would start behaving like a device registry, which is the one thing it must not
become.
"""

from __future__ import annotations

from typing import Any

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

PROVIDER_ID = "demo"

_DETAIL = (
    "Simulated devices, so the section is usable before any hub is connected. "
    "Nothing here is physical — switch it off under Connections once your own "
    "home is linked."
)


def _seed() -> list[dict[str, Any]]:
    """The starting house. A plausible small flat, not a showroom."""
    return [
        {
            "native_id": "light.living_room",
            "name": "Living room ceiling",
            "kind": DeviceKind.LIGHT,
            "room": "Living room",
            "capabilities": (
                Capability.ON_OFF,
                Capability.BRIGHTNESS,
                Capability.COLOR_TEMPERATURE,
            ),
            "state": {"on_off": True, "brightness": 70, "color_temperature": 2700},
        },
        {
            "native_id": "light.living_room_floor",
            "name": "Reading lamp",
            "kind": DeviceKind.LIGHT,
            "room": "Living room",
            "capabilities": (Capability.ON_OFF, Capability.BRIGHTNESS, Capability.COLOR),
            "state": {
                "on_off": False,
                "brightness": 40,
                "color": {"hue": 30, "saturation": 60},
            },
        },
        {
            "native_id": "climate.living_room",
            "name": "Living room heating",
            "kind": DeviceKind.CLIMATE,
            "room": "Living room",
            "capabilities": (Capability.ON_OFF, Capability.TARGET_TEMPERATURE),
            "state": {
                "on_off": True,
                "mode": "heat",
                "target_temperature": 21.0,
                "current_temperature": 20.4,
            },
            "unit": "°C",
        },
        {
            "native_id": "cover.living_room",
            "name": "Living room blinds",
            "kind": DeviceKind.COVER,
            "room": "Living room",
            "capabilities": (Capability.POSITION,),
            "state": {"position": 100, "open": True},
        },
        {
            "native_id": "light.kitchen",
            "name": "Kitchen spots",
            "kind": DeviceKind.LIGHT,
            "room": "Kitchen",
            "capabilities": (Capability.ON_OFF, Capability.BRIGHTNESS),
            "state": {"on_off": False, "brightness": 100},
        },
        {
            "native_id": "switch.kitchen_coffee",
            "name": "Coffee machine",
            "kind": DeviceKind.OUTLET,
            "room": "Kitchen",
            "capabilities": (Capability.ON_OFF,),
            "state": {"on_off": False},
        },
        {
            "native_id": "sensor.kitchen_temperature",
            "name": "Kitchen temperature",
            "kind": DeviceKind.SENSOR,
            "room": "Kitchen",
            "capabilities": (Capability.READ_ONLY,),
            "state": {"value": "21.8"},
            "unit": "°C",
        },
        {
            "native_id": "light.bedroom",
            "name": "Bedroom lamp",
            "kind": DeviceKind.LIGHT,
            "room": "Bedroom",
            "capabilities": (Capability.ON_OFF, Capability.BRIGHTNESS),
            "state": {"on_off": False, "brightness": 25},
        },
        {
            "native_id": "cover.bedroom",
            "name": "Bedroom blinds",
            "kind": DeviceKind.COVER,
            "room": "Bedroom",
            "capabilities": (Capability.POSITION,),
            "state": {"position": 0, "open": False},
        },
        {
            "native_id": "lock.front_door",
            "name": "Front door",
            "kind": DeviceKind.LOCK,
            "room": "Hallway",
            "capabilities": (Capability.LOCK,),
            "state": {"locked": True},
        },
        {
            "native_id": "binary_sensor.hallway_motion",
            "name": "Hallway motion",
            "kind": DeviceKind.BINARY_SENSOR,
            "room": "Hallway",
            "capabilities": (Capability.READ_ONLY,),
            "state": {"value": "off", "on_off": False},
        },
        {
            "native_id": "media_player.living_room",
            "name": "Living room speaker",
            "kind": DeviceKind.MEDIA_PLAYER,
            "room": "Living room",
            "capabilities": (
                Capability.ON_OFF,
                Capability.VOLUME,
                Capability.MEDIA_PLAYBACK,
            ),
            "state": {"on_off": False, "playback": "idle", "volume": 25},
        },
        {
            "native_id": "scene.good_night",
            "name": "Good night",
            "kind": DeviceKind.SCENE,
            "room": None,
            "capabilities": (Capability.ACTIVATE,),
            "state": {},
        },
        {
            "native_id": "scene.movie",
            "name": "Movie night",
            "kind": DeviceKind.SCENE,
            "room": None,
            "capabilities": (Capability.ACTIVATE,),
            "state": {},
        },
    ]


class DemoProvider:
    """An in-memory house that answers commands the way real hardware would."""

    id: str = PROVIDER_ID
    display_name: str = "Demo home"

    def __init__(self) -> None:
        self._rows = {row["native_id"]: row for row in _seed()}

    # -- helpers ------------------------------------------------------------

    def _device(self, row: dict[str, Any]) -> Device:
        room = row["room"]
        return Device(
            id=f"{PROVIDER_ID}:{row['native_id']}",
            provider=PROVIDER_ID,
            native_id=row["native_id"],
            name=row["name"],
            kind=row["kind"],
            capabilities=row["capabilities"],
            state=dict(row["state"]),
            room=room,
            room_id=f"{PROVIDER_ID}:{room}" if room else None,
            reachable=True,
            manufacturer="Jarvis Demo",
            unit=row.get("unit"),
        )

    # -- SmartHomeProvider --------------------------------------------------

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.id,
            display_name=self.display_name,
            state=ConnectionState.CONNECTED,
            detail=_DETAIL,
            device_count=len(self._rows),
            longevity="permanent",
            extra={"simulated": "true"},
        )

    async def devices(self) -> list[Device]:
        devices = [self._device(row) for row in self._rows.values()]
        devices.sort(key=lambda d: (d.room or "￿", d.name.casefold()))
        return devices

    async def rooms(self) -> list[Room]:
        counts: dict[str, int] = {}
        for row in self._rows.values():
            if row["room"]:
                counts[row["room"]] = counts.get(row["room"], 0) + 1
        return [
            Room(id=f"{PROVIDER_ID}:{name}", name=name, provider=PROVIDER_ID, device_count=n)
            for name, n in sorted(counts.items(), key=lambda kv: kv[0].casefold())
        ]

    async def execute(self, native_id: str, command: Command) -> CommandResult:
        row = self._rows.get(native_id)
        device_id = f"{PROVIDER_ID}:{native_id}"
        if row is None:
            return CommandResult(
                ok=False,
                device_id=device_id,
                command=command.name,
                error=f"No demo device called {native_id!r}.",
            )
        state: dict[str, Any] = row["state"]
        args = command.args
        name = command.name

        if name is CommandName.TURN_ON:
            state["on_off"] = True
        elif name is CommandName.TURN_OFF:
            state["on_off"] = False
        elif name is CommandName.TOGGLE:
            state["on_off"] = not state.get("on_off", False)
        elif name is CommandName.SET_BRIGHTNESS:
            state["brightness"] = max(0, min(100, int(args.get("brightness", 100))))
            # A lamp asked for brightness is a lamp asked to be on — matching
            # what every real bridge does, so the card does not show "off, 60%".
            state["on_off"] = state["brightness"] > 0
        elif name is CommandName.SET_COLOR:
            state["color"] = {
                "hue": args.get("hue", 0),
                "saturation": args.get("saturation", 100),
            }
            state["on_off"] = True
        elif name is CommandName.SET_COLOR_TEMPERATURE:
            state["color_temperature"] = int(args.get("kelvin", 3000))
            state["on_off"] = True
        elif name is CommandName.SET_TEMPERATURE:
            state["target_temperature"] = float(args.get("temperature", 21))
            state["on_off"] = True
        elif name is CommandName.SET_POSITION:
            state["position"] = max(0, min(100, int(args.get("position", 100))))
            state["open"] = state["position"] > 0
        elif name is CommandName.OPEN:
            state["position"] = 100
            state["open"] = True
        elif name is CommandName.CLOSE:
            state["position"] = 0
            state["open"] = False
        elif name is CommandName.STOP:
            pass
        elif name is CommandName.LOCK:
            state["locked"] = True
        elif name is CommandName.UNLOCK:
            state["locked"] = False
        elif name is CommandName.SET_FAN_SPEED:
            state["fan_speed"] = max(0, min(100, int(args.get("speed", 100))))
        elif name is CommandName.SET_VOLUME:
            state["volume"] = max(0, min(100, int(args.get("volume", 50))))
        elif name is CommandName.MEDIA_PLAY:
            state["playback"] = "playing"
            state["on_off"] = True
        elif name is CommandName.MEDIA_PAUSE:
            state["playback"] = "paused"
        elif name is CommandName.ACTIVATE:
            # A scene is the one command with a visible side effect elsewhere:
            # activating it has to move the lamps, or the demo teaches the wrong
            # thing about what scenes are.
            self._apply_scene(native_id)
        else:
            return CommandResult(
                ok=False,
                device_id=device_id,
                command=command.name,
                error=f"The demo home does not implement {command.name!s}.",
            )

        return CommandResult(
            ok=True, device_id=device_id, command=command.name, changed=(self._device(row),)
        )

    def _apply_scene(self, scene_id: str) -> None:
        if scene_id == "scene.good_night":
            for row in self._rows.values():
                if row["kind"] is DeviceKind.LIGHT:
                    row["state"]["on_off"] = False
                if row["kind"] is DeviceKind.COVER:
                    row["state"].update({"position": 0, "open": False})
                if row["kind"] is DeviceKind.LOCK:
                    row["state"]["locked"] = True
        elif scene_id == "scene.movie":
            for row in self._rows.values():
                if row["native_id"] == "light.living_room":
                    row["state"].update({"on_off": True, "brightness": 15})
                elif row["kind"] is DeviceKind.LIGHT:
                    row["state"]["on_off"] = False
                elif row["kind"] is DeviceKind.COVER:
                    row["state"].update({"position": 0, "open": False})
