"""The unified smart-home vocabulary: one device shape for every ecosystem.

Why this layer exists at all
---------------------------
Every smart-home platform models a lamp differently. Home Assistant calls it
``light.kitchen`` with a ``brightness`` attribute on 0-255. Philips Hue calls it
resource ``light/<uuid>`` with ``dimming.brightness`` on 0-100. Matter calls it
a ``LevelControl`` cluster. Google's SDM calls a thermostat a ``TRAIT`` bundle.
If those shapes reached the router, the voice layer and the UI, then "dim the
kitchen light" would need one code path per vendor — and adding a platform would
mean touching every one of them.

So the ecosystems stop here. A provider translates its own world into
:class:`Device` + :class:`Capability` once, and everything above — the REST
routes, the section UI, the assistant tool — only ever sees this vocabulary.

Enum policy (deliberate, see docs/anti-drift-three-layer.md)
------------------------------------------------------------
``Capability`` and ``DeviceKind`` are ``StrEnum``s here, but they cross into
JSON, TypeScript and the UI as PLAIN STRINGS, and the frontend renders an
unknown value generically instead of dropping it. That is the same call
``PluginSpec.category`` already makes in the marketplace catalog: pinning the
vocabulary in all five layers is the enum-drift bug class this repo has paid for
repeatedly, and the failure mode is silent — a device simply vanishes from the
list. Adding a capability must stay a backend-only change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DeviceKind(StrEnum):
    """What a thing *is* — drives the icon and the grouping, never the logic.

    Control decisions read :class:`Capability` instead. A "light" that cannot
    dim and a "switch" that can are handled by their capabilities; the kind only
    decides which glyph the card shows and which heading it sits under.
    """

    LIGHT = "light"
    SWITCH = "switch"
    OUTLET = "outlet"
    CLIMATE = "climate"
    COVER = "cover"
    LOCK = "lock"
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    MEDIA_PLAYER = "media_player"
    SPEAKER = "speaker"
    FAN = "fan"
    VACUUM = "vacuum"
    CAMERA = "camera"
    SCENE = "scene"
    SCRIPT = "script"
    BUTTON = "button"
    OTHER = "other"


class Capability(StrEnum):
    """What a device can actually DO — the contract the control layer reads."""

    ON_OFF = "on_off"
    BRIGHTNESS = "brightness"
    COLOR = "color"
    COLOR_TEMPERATURE = "color_temperature"
    TARGET_TEMPERATURE = "target_temperature"
    POSITION = "position"
    LOCK = "lock"
    FAN_SPEED = "fan_speed"
    VOLUME = "volume"
    MEDIA_PLAYBACK = "media_playback"
    ACTIVATE = "activate"
    #: Read-only reading (temperature, humidity, power, battery, contact, motion).
    READ_ONLY = "read_only"


class CommandName(StrEnum):
    """The verbs a caller may send. Small on purpose.

    A provider maps each verb onto its own service call. Keeping the verb set
    small is what lets the assistant speak one language: "turn the lights off"
    resolves to ``turn_off`` whether the lamp is Hue, Zigbee or a Shelly relay.
    """

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    TOGGLE = "toggle"
    SET_BRIGHTNESS = "set_brightness"
    SET_COLOR = "set_color"
    SET_COLOR_TEMPERATURE = "set_color_temperature"
    SET_TEMPERATURE = "set_temperature"
    SET_POSITION = "set_position"
    OPEN = "open"
    CLOSE = "close"
    STOP = "stop"
    LOCK = "lock"
    UNLOCK = "unlock"
    SET_FAN_SPEED = "set_fan_speed"
    SET_VOLUME = "set_volume"
    MEDIA_PLAY = "media_play"
    MEDIA_PAUSE = "media_pause"
    ACTIVATE = "activate"


#: Which verbs a capability unlocks. The UI reads this to decide which controls
#: to draw on a card, and the route layer reads it to reject a command a device
#: cannot honour BEFORE it reaches the network — a 400 with a plain reason beats
#: a vendor 500 the user cannot interpret.
COMMANDS_BY_CAPABILITY: dict[Capability, tuple[CommandName, ...]] = {
    Capability.ON_OFF: (CommandName.TURN_ON, CommandName.TURN_OFF, CommandName.TOGGLE),
    Capability.BRIGHTNESS: (CommandName.SET_BRIGHTNESS,),
    Capability.COLOR: (CommandName.SET_COLOR,),
    Capability.COLOR_TEMPERATURE: (CommandName.SET_COLOR_TEMPERATURE,),
    Capability.TARGET_TEMPERATURE: (CommandName.SET_TEMPERATURE,),
    Capability.POSITION: (
        CommandName.SET_POSITION,
        CommandName.OPEN,
        CommandName.CLOSE,
        CommandName.STOP,
    ),
    Capability.LOCK: (CommandName.LOCK, CommandName.UNLOCK),
    Capability.FAN_SPEED: (CommandName.SET_FAN_SPEED,),
    Capability.VOLUME: (CommandName.SET_VOLUME,),
    Capability.MEDIA_PLAYBACK: (CommandName.MEDIA_PLAY, CommandName.MEDIA_PAUSE),
    Capability.ACTIVATE: (CommandName.ACTIVATE,),
    Capability.READ_ONLY: (),
}


#: Commands that physically change something a person may not be able to undo
#: remotely. The safety layer decides what to do with that (§6 safety tiers);
#: this set only states WHICH commands carry the weight, so the decision is not
#: re-derived — and quietly diverging — in the UI, the route and the tool.
CONSEQUENTIAL_COMMANDS: frozenset[CommandName] = frozenset({CommandName.UNLOCK, CommandName.OPEN})


@dataclass(frozen=True, slots=True)
class Room:
    """A place devices live in. Providers supply these; Jarvis never invents one.

    ``id`` is provider-namespaced exactly like :attr:`Device.id`, because two
    ecosystems may both call a room "Kitchen" without meaning the same walls.
    """

    id: str
    name: str
    provider: str
    device_count: int = 0


@dataclass(frozen=True, slots=True)
class Device:
    """One controllable or readable thing, in Jarvis's own terms.

    ``id`` is ALWAYS ``"<provider>:<native_id>"``. Namespacing is not cosmetic:
    the moment a second ecosystem is connected, bare native ids collide (both
    Hue and a Zigbee bridge happily use ``1``), and a collision here would route
    "unlock the front door" to the wrong home.
    """

    id: str
    provider: str
    native_id: str
    name: str
    kind: DeviceKind
    capabilities: tuple[Capability, ...]
    #: Live values, keyed by capability where one exists (``on_off`` -> bool,
    #: ``brightness`` -> 0-100 int, ``target_temperature`` -> float °C) plus
    #: free-form readings for sensors. Never the vendor's raw attribute blob:
    #: normalising here is the whole point of the layer.
    state: dict[str, Any] = field(default_factory=dict)
    room: str | None = None
    room_id: str | None = None
    reachable: bool = True
    manufacturer: str | None = None
    model: str | None = None
    #: Unit for a READ_ONLY device's ``value`` (``"°C"``, ``"%"``, ``"W"``).
    unit: str | None = None

    def supports(self, command: CommandName) -> bool:
        return any(command in COMMANDS_BY_CAPABILITY.get(cap, ()) for cap in self.capabilities)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "native_id": self.native_id,
            "name": self.name,
            "kind": str(self.kind),
            "capabilities": [str(c) for c in self.capabilities],
            "commands": sorted(
                {
                    str(cmd)
                    for cap in self.capabilities
                    for cmd in COMMANDS_BY_CAPABILITY.get(cap, ())
                }
            ),
            "state": dict(self.state),
            "room": self.room,
            "room_id": self.room_id,
            "reachable": self.reachable,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class Command:
    """A verb plus its arguments, addressed at one device."""

    name: CommandName
    #: ``set_brightness`` -> ``{"brightness": 0-100}``,
    #: ``set_temperature`` -> ``{"temperature": float}``,
    #: ``set_color`` -> ``{"hue": 0-360, "saturation": 0-100}``,
    #: ``set_position`` -> ``{"position": 0-100}``.
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What actually happened — not what was requested.

    ``changed`` carries the devices whose state the platform reported back after
    the call. Reading that back is what lets the assistant say "the two kitchen
    lamps are off now" instead of "done", which is the difference between a
    report and a guess.
    """

    ok: bool
    device_id: str
    command: CommandName
    changed: tuple[Device, ...] = ()
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "device_id": self.device_id,
            "command": str(self.command),
            "changed": [d.to_json() for d in self.changed],
            "error": self.error,
        }
