"""smart_home tool — the assistant's door into the Smart Home section.

Supersedes the single-platform ``home_assistant`` tool. Both existed for one
release; only this one is router-visible, because two overlapping tools is how a
router ends up flipping a coin between "control the smart home" and "control
Home Assistant" — and picking wrong on a lock.

What changed, and why it matters at the voice layer:

* **One vocabulary, any number of platforms.** The tool speaks
  :mod:`jarvis.smarthome.models`, so "turn the lights off" resolves the same way
  whether the lamp is behind a hub, a bridge or the demo home. Adding a platform
  never touches this file.
* **Devices are addressed by NAME, not by entity id.** A person says "the
  kitchen lamp", never ``light.kitchen_ceiling_2``. Resolution happens here, and
  an ambiguous name comes back as a question with the candidates rather than a
  guess — guessing is how "unlock the door" reaches the wrong door.
* **Irreversible commands need a spoken yes.** ``unlock`` and ``open`` are
  refused unless ``confirm`` is set, so a loosely-worded sentence cannot open a
  house as a side effect. The set lives in
  :data:`jarvis.smarthome.models.CONSEQUENTIAL_COMMANDS`, never duplicated here.

Risk tier ``ask``: unlike the read-only REST plugins, a command here physically
changes something in the user's home. Turning off the wrong light is
recoverable; unlocking a door is not, so the safety layer gets to weigh in
rather than this tool deciding on its own.
"""

from __future__ import annotations

import difflib
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.smarthome.models import (
    CONSEQUENTIAL_COMMANDS,
    Command,
    CommandName,
    Device,
)
from jarvis.smarthome.registry import SmartHomeRegistry

_NOT_CONNECTED = (
    "No smart home is connected. Open the Smart Home section and connect a hub "
    "under Connections."
)

# A real house has hundreds of entities. Sending them all would blow the prompt
# budget and bury the lamp the user meant.
_LIST_LIMIT = 60

# Below this similarity a name is not a near miss, it is a different device.
# Chosen so "kitchen lamp" still finds "Kitchen lamps" while "garage" does not
# quietly resolve to "Garden".
_FUZZY_CUTOFF = 0.72


def _match(devices: list[Device], query: str) -> list[Device]:
    """Devices whose name (or room + name) the user could have meant.

    Exact and substring matches win outright. Only when neither hits does the
    fuzzy pass run — a near miss must never outrank a device the user named
    correctly.
    """
    needle = query.strip().casefold()
    if not needle:
        return []
    exact = [d for d in devices if d.name.casefold() == needle]
    if exact:
        return exact
    # An entity id is a perfectly good way to name a device when the caller
    # already read one out of a previous list_devices answer.
    by_id = [d for d in devices if d.id.casefold() == needle or d.native_id.casefold() == needle]
    if by_id:
        return by_id
    contains = [
        d
        for d in devices
        if needle in d.name.casefold()
        or needle in f"{d.room or ''} {d.name}".casefold()
    ]
    if contains:
        return contains
    names = {d.name.casefold(): d for d in devices}
    close = difflib.get_close_matches(needle, list(names), n=3, cutoff=_FUZZY_CUTOFF)
    return [names[name] for name in close]


class SmartHomeTool:
    name: str = "smart_home"
    risk_tier: str = "ask"
    description: str = (
        "Read and control the user's smart home across every connected "
        "platform: list devices, read one device's state, or send a command to "
        "it. Use for 'turn the living room lights off', 'dim the kitchen to 30 "
        "percent', 'is the garage door still open', 'set the heating to 21 "
        "degrees', 'what is the temperature in the bedroom', 'run the good "
        "night scene'. Devices are named the way the user names them ('kitchen "
        "lamp'), not by technical id. Actions: list_devices, get_device, "
        "command."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_devices", "get_device", "command"],
                "default": "list_devices",
            },
            "device": {
                "type": "string",
                "description": (
                    "the device the user named, e.g. 'kitchen lamp' or 'living "
                    "room blinds' (get_device, command)"
                ),
            },
            "room": {
                "type": "string",
                "description": "restrict list_devices to one room, e.g. 'Kitchen'",
            },
            "kind": {
                "type": "string",
                "description": (
                    "restrict list_devices to one type: light, switch, outlet, "
                    "climate, cover, lock, sensor, media_player, fan, scene"
                ),
            },
            "command": {
                "type": "string",
                "enum": [str(c) for c in CommandName],
                "description": "what to do (command)",
            },
            "args": {
                "type": "object",
                "description": (
                    "command arguments: {'brightness': 0-100}, "
                    "{'temperature': 21}, {'position': 0-100}, "
                    "{'hue': 0-360, 'saturation': 0-100}, {'volume': 0-100}"
                ),
            },
            "confirm": {
                "type": "boolean",
                "description": (
                    "set only after the user has explicitly agreed to an "
                    "irreversible action such as unlocking a door"
                ),
                "default": False,
            },
        },
        "required": ["action"],
    }

    def __init__(self, registry: SmartHomeRegistry | None = None) -> None:
        self._registry = registry

    def _reg(self) -> SmartHomeRegistry:
        # Built lazily so importing the tool never touches config or the network
        # — the entry point is loaded on the boot critical path (AP: nothing
        # heavy there).
        if self._registry is None:
            self._registry = SmartHomeRegistry()
        return self._registry

    # -- actions (directly unit-testable) -----------------------------------

    async def list_devices(
        self, *, room: str | None = None, kind: str | None = None
    ) -> dict[str, Any]:
        devices = await self._reg().devices()
        if not devices:
            return {"error": _NOT_CONNECTED}
        if room:
            needle = room.strip().casefold()
            devices = [d for d in devices if (d.room or "").casefold() == needle]
        if kind:
            needle = kind.strip().casefold()
            devices = [d for d in devices if str(d.kind) == needle]
        return {
            "total": len(devices),
            "returned": min(len(devices), _LIST_LIMIT),
            "devices": [
                {
                    "name": d.name,
                    "room": d.room,
                    "kind": str(d.kind),
                    "state": d.state,
                    "reachable": d.reachable,
                    "can": sorted(
                        {
                            str(cmd)
                            for cmd in CommandName
                            if d.supports(cmd)
                        }
                    ),
                }
                for d in devices[:_LIST_LIMIT]
            ],
        }

    async def resolve(self, query: str) -> tuple[Device | None, dict[str, Any] | None]:
        """``(device, error)`` — exactly one of the two is set."""
        devices = await self._reg().devices()
        if not devices:
            return None, {"error": _NOT_CONNECTED}
        matches = _match(devices, query)
        if not matches:
            return None, {
                "error": (
                    f"No device called {query!r}. Ask the user which device they "
                    "mean, or call list_devices to see the names."
                )
            }
        if len(matches) > 1:
            return None, {
                "error": (
                    f"{query!r} matches several devices — ask the user which one."
                ),
                "candidates": [
                    {"name": d.name, "room": d.room, "kind": str(d.kind)} for d in matches
                ],
            }
        return matches[0], None

    async def get_device(self, *, device: str) -> dict[str, Any]:
        resolved, error = await self.resolve(device)
        if error is not None:
            return error
        assert resolved is not None
        return {
            "name": resolved.name,
            "room": resolved.room,
            "kind": str(resolved.kind),
            "state": resolved.state,
            "unit": resolved.unit,
            "reachable": resolved.reachable,
        }

    async def command(
        self,
        *,
        device: str,
        command: str,
        args: dict[str, Any] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        try:
            name = CommandName(command)
        except ValueError:
            return {
                "error": (
                    f"Unknown command {command!r}. Valid commands: "
                    + ", ".join(str(c) for c in CommandName)
                )
            }
        resolved, error = await self.resolve(device)
        if error is not None:
            return error
        assert resolved is not None
        if name in CONSEQUENTIAL_COMMANDS and not confirm:
            return {
                "error": (
                    f"{name!s} on {resolved.name} physically opens something and "
                    "cannot be undone from here. Ask the user to confirm out "
                    "loud, then repeat the call with confirm set."
                ),
                "requires_confirmation": True,
            }
        result = await self._reg().execute(resolved.id, Command(name=name, args=args or {}))
        if not result.ok:
            return {"error": result.error or "That platform did not accept the command."}
        # Reading the changed devices back is what lets the assistant SAY what
        # happened ("both kitchen lamps are off now") instead of "done".
        return {
            "done": f"{name!s} on {resolved.name}",
            "changed": [
                {"name": d.name, "room": d.room, "state": d.state} for d in result.changed
            ]
            or [{"name": resolved.name, "room": resolved.room, "state": resolved.state}],
        }

    # -- Tool protocol ------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "list_devices").strip()
        try:
            if action == "list_devices":
                out = await self.list_devices(room=args.get("room"), kind=args.get("kind"))
            elif action == "get_device":
                device = args.get("device")
                if not device:
                    return ToolResult(success=False, output=None, error="device missing")
                out = await self.get_device(device=device)
            elif action == "command":
                device = args.get("device")
                command = args.get("command")
                if not device or not command:
                    return ToolResult(
                        success=False,
                        output=None,
                        error="command needs both device and command",
                    )
                raw_args = args.get("args")
                out = await self.command(
                    device=device,
                    command=command,
                    args=raw_args if isinstance(raw_args, dict) else None,
                    confirm=bool(args.get("confirm")),
                )
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception:  # noqa: BLE001
            # Never `str(exc)`: a platform's error body can echo a token back,
            # and this string reaches the transcript and the logs.
            return ToolResult(
                success=False,
                output=None,
                error="The smart home did not answer that request.",
            )

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=out, error=out["error"])
        return ToolResult(success=True, output=out)
