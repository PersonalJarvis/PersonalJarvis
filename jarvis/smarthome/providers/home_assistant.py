"""Home Assistant as the universal adapter.

Why the hub is the first-class connector
----------------------------------------
Home Assistant already speaks around 2000 protocols — Zigbee, Z-Wave, Matter,
Thread, KNX, Hue, Shelly, Tuya, Nest, HomeKit. Writing one adapter against it
buys more real coverage than writing twenty vendor adapters, and it buys the
part nobody enjoys: pairing, discovery and firmware quirks stay in the hub.

Two properties shape this file:

* **The address is user data.** Home Assistant runs on the user's own network,
  so the base URL is stored per connection rather than hardcoded, and a failure
  to reach it is a *state* (``UNREACHABLE``), not an error to show as a stack
  trace. Never assume the ``.local`` hostname — mDNS does not resolve inside
  Docker or over a VPN.
* **The credential is permanent.** A Long-Lived Access Token is valid for ten
  years with no rotation. That is why this connector answers the maintainer's
  actual complaint about re-authorizing every week: there is nothing to renew.

Areas need the template API
---------------------------
Home Assistant's REST surface exposes states but not areas, so rooms are read by
POSTing one Jinja template to ``/api/template`` that maps every entity to its
area. It is a documented endpoint and one round trip. When it fails (an older
core, a token without template rights) rooms degrade to empty rather than taking
the device list down with them — a house with no room labels is still usable.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
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

log = logging.getLogger(__name__)

PROVIDER_ID = "home_assistant"

# One request that answers "which entity is in which room". `area_name` is a
# core template function; entities with no area are skipped rather than bucketed
# into a fake "Unassigned" room the user never created.
_AREA_TEMPLATE = (
    "{%- set ns = namespace(rows=[]) -%}"
    "{%- for s in states -%}"
    "{%- set a = area_name(s.entity_id) -%}"
    "{%- if a -%}"
    "{%- set ns.rows = ns.rows + [{'e': s.entity_id, 'a': a}] -%}"
    "{%- endif -%}"
    "{%- endfor -%}"
    "{{ ns.rows | tojson }}"
)

# Domain -> (kind, base capabilities). Capabilities are then refined per entity
# from its attributes, because two lights in the same house differ: a filament
# bulb has no colour, a smart plug reporting watts is really a sensor.
_DOMAIN_MAP: dict[str, tuple[DeviceKind, tuple[Capability, ...]]] = {
    "light": (DeviceKind.LIGHT, (Capability.ON_OFF,)),
    "switch": (DeviceKind.SWITCH, (Capability.ON_OFF,)),
    "input_boolean": (DeviceKind.SWITCH, (Capability.ON_OFF,)),
    "climate": (DeviceKind.CLIMATE, (Capability.ON_OFF, Capability.TARGET_TEMPERATURE)),
    "water_heater": (DeviceKind.CLIMATE, (Capability.TARGET_TEMPERATURE,)),
    "cover": (DeviceKind.COVER, (Capability.POSITION,)),
    "lock": (DeviceKind.LOCK, (Capability.LOCK,)),
    "sensor": (DeviceKind.SENSOR, (Capability.READ_ONLY,)),
    "binary_sensor": (DeviceKind.BINARY_SENSOR, (Capability.READ_ONLY,)),
    "media_player": (
        DeviceKind.MEDIA_PLAYER,
        (Capability.ON_OFF, Capability.VOLUME, Capability.MEDIA_PLAYBACK),
    ),
    "fan": (DeviceKind.FAN, (Capability.ON_OFF, Capability.FAN_SPEED)),
    "vacuum": (DeviceKind.VACUUM, (Capability.ON_OFF,)),
    "camera": (DeviceKind.CAMERA, (Capability.READ_ONLY,)),
    "scene": (DeviceKind.SCENE, (Capability.ACTIVATE,)),
    "script": (DeviceKind.SCRIPT, (Capability.ACTIVATE,)),
    "button": (DeviceKind.BUTTON, (Capability.ACTIVATE,)),
    "input_button": (DeviceKind.BUTTON, (Capability.ACTIVATE,)),
    "humidifier": (DeviceKind.CLIMATE, (Capability.ON_OFF,)),
}

# Domains that exist in every Home Assistant and describe the SYSTEM rather than
# the home. Showing "Sun", "Backup" or an update entity in a room list is noise
# in front of the lamp the user came for.
_SKIPPED_DOMAINS: frozenset[str] = frozenset(
    {
        "automation",
        "conversation",
        "device_tracker",
        "event",
        "number",
        "persistent_notification",
        "person",
        "select",
        "stt",
        "sun",
        "text",
        "tts",
        "todo",
        "update",
        "zone",
    }
)

# Colour modes that mean "this light can show a colour" vs "only warmer/cooler".
_COLOR_MODES: frozenset[str] = frozenset({"hs", "rgb", "rgbw", "rgbww", "xy"})
_DIMMABLE_MODES: frozenset[str] = frozenset(
    {"brightness", "color_temp", "hs", "rgb", "rgbw", "rgbww", "xy", "white"}
)

_NOT_CONNECTED = (
    "Home Assistant is not connected yet. Connect it under Connections and "
    "Jarvis can see everything your hub can see."
)


def _default_connection() -> tuple[str | None, str | None]:
    """Read the address + token stored by the marketplace connect flow.

    Reusing the marketplace credential on purpose: the user connected Home
    Assistant once, and asking them to do it a second time for a different
    screen would be the kind of duplicated setup this section exists to remove.
    """
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load(PROVIDER_ID)
    if tokens is None:
        return None, None
    return tokens.extra.get("instance_url"), tokens.access


def _as_percent(raw: object, scale: int) -> int | None:
    """Rescale a vendor value onto 0-100, the only brightness unit above here."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max(0, min(100, round(value * 100 / scale)))


def _as_float(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _refine_light(attrs: dict[str, Any], caps: list[Capability]) -> None:
    """Add the capabilities this particular lamp actually has."""
    modes = {str(m) for m in (attrs.get("supported_color_modes") or [])}
    if modes & _DIMMABLE_MODES:
        caps.append(Capability.BRIGHTNESS)
    if modes & _COLOR_MODES:
        caps.append(Capability.COLOR)
    if "color_temp" in modes:
        caps.append(Capability.COLOR_TEMPERATURE)


def entity_to_device(entity: dict[str, Any], area_by_entity: dict[str, str]) -> Device | None:
    """Translate one Home Assistant state object into the unified model.

    Returns ``None`` for entities that are not part of the home (system
    domains), so the caller can simply filter.
    """
    entity_id = str(entity.get("entity_id") or "")
    if "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0]
    if domain in _SKIPPED_DOMAINS:
        return None

    attrs: dict[str, Any] = entity.get("attributes") or {}
    kind, base_caps = _DOMAIN_MAP.get(domain, (DeviceKind.OTHER, (Capability.READ_ONLY,)))
    caps = list(base_caps)
    raw_state = entity.get("state")
    state: dict[str, Any] = {}
    unit: str | None = attrs.get("unit_of_measurement")

    if domain == "light":
        _refine_light(attrs, caps)
        state["on_off"] = raw_state == "on"
        brightness = _as_percent(attrs.get("brightness"), 255)
        if brightness is not None:
            state["brightness"] = brightness
        kelvin = attrs.get("color_temp_kelvin")
        if kelvin is not None:
            state["color_temperature"] = kelvin
        hs = attrs.get("hs_color")
        if isinstance(hs, list | tuple) and len(hs) == 2:
            state["color"] = {"hue": hs[0], "saturation": hs[1]}
    elif domain in {"switch", "input_boolean", "fan", "humidifier"}:
        state["on_off"] = raw_state == "on"
        percentage = attrs.get("percentage")
        if percentage is not None:
            state["fan_speed"] = _as_percent(percentage, 100)
    elif domain in {"climate", "water_heater"}:
        state["on_off"] = raw_state not in {"off", "unavailable", "unknown"}
        state["mode"] = raw_state
        target = _as_float(attrs.get("temperature"))
        if target is not None:
            state["target_temperature"] = target
        current = _as_float(attrs.get("current_temperature"))
        if current is not None:
            state["current_temperature"] = current
        unit = unit or "°C"
    elif domain == "cover":
        position = attrs.get("current_position")
        state["position"] = _as_percent(position, 100) if position is not None else None
        state["open"] = raw_state == "open"
    elif domain == "lock":
        state["locked"] = raw_state == "locked"
    elif domain == "media_player":
        state["on_off"] = raw_state not in {"off", "standby", "unavailable", "unknown"}
        state["playback"] = raw_state
        volume = _as_float(attrs.get("volume_level"))
        if volume is not None:
            state["volume"] = round(volume * 100)
    elif domain in {"sensor", "binary_sensor"}:
        state["value"] = raw_state
        if domain == "binary_sensor":
            state["on_off"] = raw_state == "on"
    elif domain == "vacuum":
        state["on_off"] = raw_state in {"cleaning", "returning"}
        state["activity"] = raw_state

    # `unavailable` is Home Assistant's word for "the integration cannot see the
    # hardware right now" — a dead battery, an unplugged bridge. Surfacing that
    # as a greyed-out card is far more useful than showing a lamp as "off".
    reachable = raw_state not in {"unavailable", None}

    # Smart plugs commonly expose a `device_class` of outlet; using it keeps the
    # icon honest without a second entity lookup.
    if domain == "switch" and attrs.get("device_class") == "outlet":
        kind = DeviceKind.OUTLET

    return Device(
        id=f"{PROVIDER_ID}:{entity_id}",
        provider=PROVIDER_ID,
        native_id=entity_id,
        name=str(attrs.get("friendly_name") or entity_id),
        kind=kind,
        capabilities=tuple(dict.fromkeys(caps)),
        state=state,
        room=area_by_entity.get(entity_id),
        room_id=(
            f"{PROVIDER_ID}:{area_by_entity[entity_id]}" if entity_id in area_by_entity else None
        ),
        reachable=reachable,
        unit=unit,
    )


def command_to_service(native_id: str, command: Command) -> tuple[str, str, dict[str, Any]] | None:
    """Map a unified verb onto ``(domain, service, data)``.

    Returns ``None`` when the verb has no Home Assistant equivalent for that
    entity, which the caller turns into a plain refusal instead of a 500.
    """
    domain = native_id.split(".", 1)[0]
    args = command.args
    name = command.name

    if name is CommandName.TURN_ON:
        return domain, "turn_on", {}
    if name is CommandName.TURN_OFF:
        return domain, "turn_off", {}
    if name is CommandName.TOGGLE:
        # The `homeassistant` pseudo-domain toggles anything toggleable, which
        # saves a per-domain branch and works for entities we do not model.
        return "homeassistant", "toggle", {}
    if name is CommandName.SET_BRIGHTNESS:
        return "light", "turn_on", {"brightness_pct": args.get("brightness", 100)}
    if name is CommandName.SET_COLOR:
        return (
            "light",
            "turn_on",
            {"hs_color": [args.get("hue", 0), args.get("saturation", 100)]},
        )
    if name is CommandName.SET_COLOR_TEMPERATURE:
        return "light", "turn_on", {"color_temp_kelvin": args.get("kelvin", 3000)}
    if name is CommandName.SET_TEMPERATURE:
        return domain, "set_temperature", {"temperature": args.get("temperature")}
    if name is CommandName.SET_POSITION:
        return "cover", "set_cover_position", {"position": args.get("position", 100)}
    if name is CommandName.OPEN:
        return "cover", "open_cover", {}
    if name is CommandName.CLOSE:
        return "cover", "close_cover", {}
    if name is CommandName.STOP:
        return "cover", "stop_cover", {}
    if name is CommandName.LOCK:
        return "lock", "lock", {}
    if name is CommandName.UNLOCK:
        return "lock", "unlock", {}
    if name is CommandName.SET_FAN_SPEED:
        return "fan", "set_percentage", {"percentage": args.get("speed", 100)}
    if name is CommandName.SET_VOLUME:
        volume = args.get("volume", 50)
        return "media_player", "volume_set", {"volume_level": max(0, min(100, volume)) / 100}
    if name is CommandName.MEDIA_PLAY:
        return "media_player", "media_play", {}
    if name is CommandName.MEDIA_PAUSE:
        return "media_player", "media_pause", {}
    if name is CommandName.ACTIVATE:
        if domain == "button" or domain == "input_button":
            return domain, "press", {}
        return domain, "turn_on", {}
    return None


class HomeAssistantProvider:
    """The unified-model face of a Home Assistant instance."""

    id: str = PROVIDER_ID
    display_name: str = "Home Assistant"

    def __init__(
        self,
        connection_provider: Callable[[], tuple[str | None, str | None]] | None = None,
        transport: Any | None = None,
    ) -> None:
        from jarvis.plugins.tool._http_pool import HttpClientPool

        self._connection = connection_provider or _default_connection
        self._pool = HttpClientPool(transport=transport)

    # -- plumbing -----------------------------------------------------------

    def _resolve(self) -> tuple[str, dict[str, str]] | None:
        base, token = self._connection()
        if not base or not token:
            return None
        return base.rstrip("/"), {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Personal-Jarvis/1.0",
        }

    async def _areas(self, base: str, headers: dict[str, str]) -> dict[str, str]:
        """entity_id -> area name. Empty on any failure, never raising."""
        try:
            client = self._pool.client()
            resp = await client.post(
                f"{base}/api/template", headers=headers, json={"template": _AREA_TEMPLATE}
            )
            resp.raise_for_status()
            rows = json.loads(resp.text)
        except Exception:  # noqa: BLE001 — rooms are a nicety, devices are not
            log.debug("smarthome: area template unavailable", exc_info=True)
            return {}
        if not isinstance(rows, list):
            return {}
        return {
            str(row["e"]): str(row["a"])
            for row in rows
            if isinstance(row, dict) and row.get("e") and row.get("a")
        }

    # -- SmartHomeProvider --------------------------------------------------

    async def status(self) -> ProviderStatus:
        resolved = self._resolve()
        if resolved is None:
            return ProviderStatus(
                provider=self.id,
                display_name=self.display_name,
                state=ConnectionState.NOT_CONFIGURED,
                detail=_NOT_CONNECTED,
                longevity="permanent",
            )
        base, headers = resolved
        try:
            client = self._pool.client()
            resp = await client.get(f"{base}/api/", headers=headers)
        except Exception as exc:  # noqa: BLE001
            return ProviderStatus(
                provider=self.id,
                display_name=self.display_name,
                state=ConnectionState.UNREACHABLE,
                detail=self._explain(exc),
                longevity="permanent",
                extra={"instance_url": base},
            )
        if resp.status_code in {401, 403}:
            return ProviderStatus(
                provider=self.id,
                display_name=self.display_name,
                state=ConnectionState.NEEDS_REAUTH,
                detail=(
                    "Home Assistant rejected the access token. Create a new "
                    "long-lived token in your Home Assistant profile and "
                    "reconnect."
                ),
                longevity="permanent",
                extra={"instance_url": base},
            )
        if resp.status_code >= 400:
            return ProviderStatus(
                provider=self.id,
                display_name=self.display_name,
                state=ConnectionState.UNREACHABLE,
                detail=(
                    f"Home Assistant answered with status {resp.status_code} at "
                    "that address. Check that the address points at Home "
                    "Assistant itself and not at a proxy in front of it."
                ),
                longevity="permanent",
                extra={"instance_url": base},
            )
        return ProviderStatus(
            provider=self.id,
            display_name=self.display_name,
            state=ConnectionState.CONNECTED,
            longevity="permanent",
            extra={"instance_url": base},
        )

    async def devices(self) -> list[Device]:
        resolved = self._resolve()
        if resolved is None:
            return []
        base, headers = resolved
        client = self._pool.client()
        resp = await client.get(f"{base}/api/states", headers=headers)
        resp.raise_for_status()
        states = resp.json()
        if not isinstance(states, list):
            return []
        areas = await self._areas(base, headers)
        devices = [
            device
            for entity in states
            if isinstance(entity, dict) and (device := entity_to_device(entity, areas)) is not None
        ]
        devices.sort(key=lambda d: (d.room or "￿", d.name.casefold()))
        return devices

    async def rooms(self) -> list[Room]:
        resolved = self._resolve()
        if resolved is None:
            return []
        base, headers = resolved
        areas = await self._areas(base, headers)
        counts: dict[str, int] = {}
        for entity_id, area in areas.items():
            if entity_id.split(".", 1)[0] not in _SKIPPED_DOMAINS:
                counts[area] = counts.get(area, 0) + 1
        return [
            Room(id=f"{PROVIDER_ID}:{name}", name=name, provider=PROVIDER_ID, device_count=n)
            for name, n in sorted(counts.items(), key=lambda kv: kv[0].casefold())
        ]

    async def execute(self, native_id: str, command: Command) -> CommandResult:
        resolved = self._resolve()
        if resolved is None:
            return CommandResult(
                ok=False,
                device_id=f"{PROVIDER_ID}:{native_id}",
                command=command.name,
                error=_NOT_CONNECTED,
            )
        mapped = command_to_service(native_id, command)
        if mapped is None:
            return CommandResult(
                ok=False,
                device_id=f"{PROVIDER_ID}:{native_id}",
                command=command.name,
                error=f"Home Assistant has no equivalent for {command.name!s} here.",
            )
        domain, service, data = mapped
        base, headers = resolved
        payload = {k: v for k, v in data.items() if v is not None}
        payload["entity_id"] = native_id
        try:
            client = self._pool.client()
            resp = await client.post(
                f"{base}/api/services/{domain}/{service}", headers=headers, json=payload
            )
            resp.raise_for_status()
            changed_raw = resp.json()
        except Exception as exc:  # noqa: BLE001
            return CommandResult(
                ok=False,
                device_id=f"{PROVIDER_ID}:{native_id}",
                command=command.name,
                error=self._explain(exc),
            )
        changed = (
            tuple(
                device
                for entity in changed_raw
                if isinstance(entity, dict) and (device := entity_to_device(entity, {})) is not None
            )
            if isinstance(changed_raw, list)
            else ()
        )
        return CommandResult(
            ok=True,
            device_id=f"{PROVIDER_ID}:{native_id}",
            command=command.name,
            changed=changed,
        )

    @staticmethod
    def _explain(exc: Exception) -> str:
        """Turn a transport failure into something a person can act on.

        Home Assistant lives on the home network, so the common failure is not a
        bad token but an unreachable server — Jarvis on another machine, a VPN,
        a container. "ConnectError" tells the user nothing; naming the likely
        cause tells them what to check.
        """
        import httpx

        if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
            return (
                "Could not reach Home Assistant. It runs on your home network, "
                "so check that this machine can reach that address — a VPN, a "
                "container or a remote server will not see it."
            )
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}:
            return (
                "Home Assistant rejected the access token. Create a new "
                "long-lived token in your Home Assistant profile and reconnect."
            )
        # Deliberately not `str(exc)`: a vendor error body can echo the token
        # back, and this string reaches both the UI and the logs.
        return "Home Assistant did not accept that request."
