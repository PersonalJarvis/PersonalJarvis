"""The house as the USER arranged it — Jarvis's own room layer.

Why this exists
---------------
Until this module, a "room" was whatever a hub happened to report, and the
section could only ever mirror it. That made three things impossible at once:
a room could not be created (there was nowhere to put it), it could not carry
anything a hub does not model (an icon, a colour, an order, a favourite), and a
house with no hub at all had no rooms whatsoever.

So Jarvis now OWNS the arrangement, and the platforms keep owning the hardware.
The split matters:

* A **provider room** ("Home Assistant area ``living_room``") is a fact about
  someone else's system. Jarvis reads it and never writes it back.
* A **room** here is the user's own idea of a place. It may adopt one or several
  provider rooms, pin individual devices by hand, and exclude ones that were
  swept in by an adoption.

That is what lets "Wohnzimmer" be one room even when the lamps live in a Hue
bridge's *Living room* and the radiator lives in a Home Assistant area spelled
differently — a merge the old mirror-only design could not express at all.

First run, and why it is not repeated
-------------------------------------
The very first read adopts whatever the platforms report, so a connected hub
still lands on a furnished screen with no setup. After that the layout belongs
to the user: a newly appearing provider room is offered as a SUGGESTION and
never adopted silently. Re-adopting on every read would resurrect a room the
user deliberately deleted and rename one they deliberately renamed — the
restore-trap bug class this repo has paid for before.

One device, one room
--------------------
:func:`resolve` guarantees a device appears under exactly one room. A pinned
device wins over an adopted one, an exclusion wins over both, and whatever is
left over is reported separately rather than dropped — a device that is silently
missing from every room is a device the user cannot switch off.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir
from jarvis.smarthome.models import Device, Room

log = logging.getLogger(__name__)

#: Bumped only when the on-disk shape changes incompatibly.
LAYOUT_VERSION = 1

#: Icon slugs the room editor offers. Kept as PLAIN STRINGS rather than an enum
#: on purpose: the frontend maps an unknown slug to a neutral glyph instead of
#: dropping the room, which is the same tolerance ``deviceMeta.iconForKind``
#: already applies to device kinds. Adding one here stays a backend-only change.
ROOM_ICONS: tuple[str, ...] = (
    "room",
    "living_room",
    "kitchen",
    "bedroom",
    "bathroom",
    "office",
    "dining",
    "hallway",
    "kids",
    "garage",
    "garden",
    "basement",
    "attic",
    "laundry",
    "balcony",
    "stairs",
)

#: Colour tokens, NOT hex values. A hex chosen against a light background is
#: usually unreadable on a dark one, and every surface in this app has to work
#: in both; the frontend resolves each token to a pair of theme-aware classes.
ROOM_COLORS: tuple[str, ...] = (
    "slate",
    "amber",
    "rose",
    "violet",
    "sky",
    "emerald",
    "orange",
    "teal",
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_MAX_NAME = 60
_MAX_FLOOR = 40


def _slugify(name: str) -> str:
    """A stable, filesystem-safe id from a display name.

    Non-ASCII room names are ordinary — a hub speaks whatever language its
    owner set up — and a slug that collapsed those to nothing would leave
    rooms sharing the empty id. The fallback keeps ids unique without
    inventing a transliteration table.
    """
    lowered = (name or "").strip().casefold()
    # Fold the umlauts explicitly: they are the common case for this app's
    # users, and a folded slug reads far better in a URL than a hyphen gap.
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):  # i18n-allow
        lowered = lowered.replace(source, target)
    slug = _SLUG_STRIP.sub("-", lowered).strip("-")
    return slug or "room"


def _clean_name(raw: str, *, field_name: str = "name", limit: int = _MAX_NAME) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"A room needs a {field_name}.")
    if len(value) > limit:
        raise ValueError(f"That {field_name} is too long (max {limit} characters).")
    return value


def _dedupe(values: object) -> tuple[str, ...]:
    """Trim, drop blanks, preserve order, remove repeats."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values if isinstance(values, (list, tuple)) else ():
        value = str(raw).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class RoomLayout:
    """One place in the house, as the user arranged it.

    Everything beyond ``id``/``name`` is presentation or grouping — none of it
    is ever sent to a platform. That is deliberate: this layer must not be able
    to corrupt someone's hub configuration by existing.
    """

    id: str
    name: str
    icon: str = "room"
    color: str = "slate"
    #: Free-text storey ("Ground floor", "Upstairs"). Optional and unvalidated
    #: beyond a length cap — houses are not a fixed taxonomy.
    floor: str | None = None
    order: int = 0
    #: Provider room ids this room adopts. Several on purpose: one place in a
    #: home is frequently two areas in two ecosystems.
    provider_rooms: tuple[str, ...] = ()
    #: Device ids pinned by hand. Wins over adoption, so a lamp can be moved
    #: into the room it is physically in even when the hub disagrees.
    devices: tuple[str, ...] = ()
    #: Devices swept in by an adoption that do NOT belong here.
    excluded_devices: tuple[str, ...] = ()
    #: Shown first and larger — the handful someone actually touches.
    favorites: tuple[str, ...] = ()
    #: Which sensor speaks for the room in its header. Without this the room
    #: would have to guess between three thermometers and would guess wrong.
    temperature_device: str | None = None
    humidity_device: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "color": self.color,
            "floor": self.floor,
            "order": self.order,
            "provider_rooms": list(self.provider_rooms),
            "devices": list(self.devices),
            "excluded_devices": list(self.excluded_devices),
            "favorites": list(self.favorites),
            "temperature_device": self.temperature_device,
            "humidity_device": self.humidity_device,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> RoomLayout | None:
        """Rebuild from disk, tolerating a hand-edited or older file.

        Returns ``None`` for an entry too damaged to be a room. A single bad
        entry must not take the whole house down with it — the caller skips it
        and keeps the rest.
        """
        room_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not room_id or not name:
            return None
        icon = str(raw.get("icon") or "room").strip() or "room"
        color = str(raw.get("color") or "slate").strip() or "slate"
        floor_raw = raw.get("floor")
        floor = str(floor_raw).strip() if floor_raw else None
        try:
            order = int(raw.get("order") or 0)
        except (TypeError, ValueError):
            order = 0
        temperature = raw.get("temperature_device")
        humidity = raw.get("humidity_device")
        return cls(
            id=room_id,
            name=name[:_MAX_NAME],
            icon=icon,
            color=color,
            floor=floor[:_MAX_FLOOR] if floor else None,
            order=order,
            provider_rooms=_dedupe(raw.get("provider_rooms")),
            devices=_dedupe(raw.get("devices")),
            excluded_devices=_dedupe(raw.get("excluded_devices")),
            favorites=_dedupe(raw.get("favorites")),
            temperature_device=str(temperature).strip() if temperature else None,
            humidity_device=str(humidity).strip() if humidity else None,
        )


@dataclass(frozen=True, slots=True)
class HomeLayout:
    """Every room, in display order."""

    rooms: tuple[RoomLayout, ...] = ()
    version: int = LAYOUT_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "rooms": [room.to_json() for room in self.ordered()],
        }

    def ordered(self) -> tuple[RoomLayout, ...]:
        """Rooms by explicit order, then by name — never by dict insertion.

        Ties are broken by name so two rooms sharing an order (easy to produce
        by hand-editing the file) keep a stable position between reads instead
        of swapping places on every refresh.
        """
        return tuple(sorted(self.rooms, key=lambda r: (r.order, r.name.casefold())))

    def get(self, room_id: str) -> RoomLayout | None:
        return next((r for r in self.rooms if r.id == room_id), None)


@dataclass
class HomeLayoutStore:
    """Reads and writes ``<user data>/data/smarthome/home.json``.

    ``path`` is injectable for tests. Writes are atomic (tempfile in the same
    directory, then ``os.replace``) — the same rule the contacts store and the
    TOML writer follow, and for the same reason: a half-written layout would
    lose every room the user ever arranged.
    """

    path: Path | None = None

    def __post_init__(self) -> None:
        if self.path is None:
            self.path = user_data_dir() / "data" / "smarthome" / "home.json"
        else:
            self.path = Path(self.path)

    # -- io ------------------------------------------------------------------

    def exists(self) -> bool:
        """Has the user ever had a layout? Decides first-run adoption."""
        return self.path is not None and self.path.exists()

    def load(self) -> HomeLayout:
        if self.path is None or not self.path.exists():
            return HomeLayout()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt file must not blank the section. The user sees an empty
            # layout and can rebuild; the broken file stays for inspection.
            log.warning("smarthome: could not read %s, ignoring it", self.path, exc_info=True)
            return HomeLayout()
        rooms: list[RoomLayout] = []
        for entry in raw.get("rooms") or []:
            if isinstance(entry, dict):
                room = RoomLayout.from_json(entry)
                if room is not None:
                    rooms.append(room)
        return HomeLayout(rooms=tuple(rooms), version=int(raw.get("version") or LAYOUT_VERSION))

    def save(self, layout: HomeLayout) -> HomeLayout:
        assert self.path is not None  # set in __post_init__
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        text = json.dumps(layout.to_json(), indent=2, ensure_ascii=False) + "\n"
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return layout

    # -- mutations -----------------------------------------------------------

    def _unique_id(self, layout: HomeLayout, name: str) -> str:
        base = _slugify(name)
        candidate = base
        index = 2
        taken = {room.id for room in layout.rooms}
        while candidate in taken:
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def create_room(
        self,
        *,
        name: str,
        icon: str = "room",
        color: str = "slate",
        floor: str | None = None,
        provider_rooms: list[str] | None = None,
        devices: list[str] | None = None,
    ) -> RoomLayout:
        layout = self.load()
        clean_name = _clean_name(name)
        if any(room.name.casefold() == clean_name.casefold() for room in layout.rooms):
            raise ValueError(f"There is already a room called {clean_name!r}.")
        # New rooms go to the END, which is where someone adding one expects to
        # find it — not at the top, pushing the arrangement they already made.
        next_order = max((room.order for room in layout.rooms), default=-1) + 1
        room = RoomLayout(
            id=self._unique_id(layout, clean_name),
            name=clean_name,
            icon=icon.strip() or "room",
            color=color.strip() or "slate",
            floor=_clean_name(floor, field_name="floor", limit=_MAX_FLOOR) if floor else None,
            order=next_order,
            provider_rooms=_dedupe(provider_rooms),
            devices=_dedupe(devices),
        )
        room = self._claim(layout, room)
        self.save(HomeLayout(rooms=(*layout.rooms, room)))
        return room

    def update_room(self, room_id: str, **fields: Any) -> RoomLayout | None:
        """Patch one room. Only keys present AND not ``None`` are written."""
        layout = self.load()
        current = layout.get(room_id)
        if current is None:
            return None

        changes: dict[str, Any] = {}
        if (name := fields.get("name")) is not None:
            clean_name = _clean_name(name)
            clash = any(
                room.name.casefold() == clean_name.casefold() and room.id != room_id
                for room in layout.rooms
            )
            if clash:
                raise ValueError(f"There is already a room called {clean_name!r}.")
            changes["name"] = clean_name
        if (icon := fields.get("icon")) is not None:
            changes["icon"] = str(icon).strip() or "room"
        if (color := fields.get("color")) is not None:
            changes["color"] = str(color).strip() or "slate"
        if "floor" in fields:
            floor = fields.get("floor")
            # An empty string clears the storey; ``None`` means "not supplied".
            if floor is not None:
                cleaned = str(floor).strip()
                changes["floor"] = cleaned[:_MAX_FLOOR] or None
        for key in ("provider_rooms", "devices", "excluded_devices", "favorites"):
            if fields.get(key) is not None:
                changes[key] = _dedupe(fields[key])
        for key in ("temperature_device", "humidity_device"):
            if key in fields:
                value = fields.get(key)
                changes[key] = str(value).strip() or None if value is not None else None

        updated = replace(current, **changes)
        rooms = tuple(updated if room.id == room_id else room for room in layout.rooms)
        # A device pinned here must not stay pinned somewhere else.
        if "devices" in changes or "provider_rooms" in changes:
            updated = self._claim(HomeLayout(rooms=rooms), updated)
            rooms = tuple(
                self._release(room, updated) if room.id != room_id else updated for room in rooms
            )
        self.save(HomeLayout(rooms=rooms))
        return updated

    def delete_room(self, room_id: str) -> bool:
        layout = self.load()
        if layout.get(room_id) is None:
            return False
        # Devices are NOT deleted with the room — they return to "unassigned"
        # and stay switchable. A device that disappears with its room would be
        # a device the user can no longer turn off.
        self.save(HomeLayout(rooms=tuple(r for r in layout.rooms if r.id != room_id)))
        return True

    def reorder(self, room_ids: list[str]) -> HomeLayout:
        """Apply a new display order. Ids not listed keep their relative tail."""
        layout = self.load()
        wanted = [rid for rid in _dedupe(room_ids) if layout.get(rid) is not None]
        position = {rid: index for index, rid in enumerate(wanted)}
        tail = len(position)
        rooms: list[RoomLayout] = []
        for room in layout.ordered():
            if room.id in position:
                rooms.append(replace(room, order=position[room.id]))
            else:
                rooms.append(replace(room, order=tail))
                tail += 1
        return self.save(HomeLayout(rooms=tuple(rooms)))

    def assign_devices(self, room_id: str, device_ids: list[str]) -> RoomLayout | None:
        """Pin devices into a room, taking them out of whichever room had them."""
        layout = self.load()
        target = layout.get(room_id)
        if target is None:
            return None
        incoming = _dedupe(device_ids)
        if not incoming:
            return target
        updated = replace(
            target,
            devices=_dedupe([*target.devices, *incoming]),
            # Pinning overrides a previous exclusion — the later, more explicit
            # gesture wins, otherwise re-adding a device would silently no-op.
            excluded_devices=tuple(d for d in target.excluded_devices if d not in incoming),
        )
        rooms = tuple(
            updated if room.id == room_id else self._release(room, updated) for room in layout.rooms
        )
        self.save(HomeLayout(rooms=rooms))
        return updated

    def remove_device(self, room_id: str, device_id: str) -> RoomLayout | None:
        """Take one device out of a room.

        Un-pinning alone is not enough: if the room adopted the provider area
        the device sits in, it would simply reappear on the next read. So the
        removal is also recorded as an exclusion.
        """
        layout = self.load()
        target = layout.get(room_id)
        if target is None:
            return None
        updated = replace(
            target,
            devices=tuple(d for d in target.devices if d != device_id),
            excluded_devices=_dedupe([*target.excluded_devices, device_id]),
            favorites=tuple(d for d in target.favorites if d != device_id),
            temperature_device=(
                None if target.temperature_device == device_id else target.temperature_device
            ),
            humidity_device=(
                None if target.humidity_device == device_id else target.humidity_device
            ),
        )
        self.save(HomeLayout(rooms=tuple(updated if r.id == room_id else r for r in layout.rooms)))
        return updated

    def toggle_favorite(self, room_id: str, device_id: str) -> RoomLayout | None:
        layout = self.load()
        target = layout.get(room_id)
        if target is None:
            return None
        favorites = (
            tuple(d for d in target.favorites if d != device_id)
            if device_id in target.favorites
            else (*target.favorites, device_id)
        )
        updated = replace(target, favorites=favorites)
        self.save(HomeLayout(rooms=tuple(updated if r.id == room_id else r for r in layout.rooms)))
        return updated

    def adopt(self, provider_rooms: list[Room]) -> HomeLayout:
        """Take provider areas over as rooms, skipping ones already adopted.

        Used for the first run and for the explicit "import" the UI offers when
        a hub grows a new area. Areas with the same NAME across two platforms
        become ONE room — that is the merge the old mirror could not express.
        """
        layout = self.load()
        claimed = {pid for room in layout.rooms for pid in room.provider_rooms}
        by_name = {room.name.casefold(): room for room in layout.rooms}
        rooms = list(layout.rooms)
        next_order = max((room.order for room in rooms), default=-1) + 1

        for provider_room in provider_rooms:
            if provider_room.id in claimed:
                continue
            existing = by_name.get(provider_room.name.casefold())
            if existing is not None:
                merged = replace(
                    existing,
                    provider_rooms=_dedupe([*existing.provider_rooms, provider_room.id]),
                )
                rooms = [merged if r.id == existing.id else r for r in rooms]
                by_name[merged.name.casefold()] = merged
                continue
            created = RoomLayout(
                id=self._unique_id(HomeLayout(rooms=tuple(rooms)), provider_room.name),
                name=provider_room.name[:_MAX_NAME],
                icon=suggest_icon(provider_room.name),
                color=suggest_color(provider_room.name),
                order=next_order,
                provider_rooms=(provider_room.id,),
            )
            next_order += 1
            rooms.append(created)
            by_name[created.name.casefold()] = created

        return self.save(HomeLayout(rooms=tuple(rooms)))

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _claim(layout: HomeLayout, room: RoomLayout) -> RoomLayout:
        """Drop pins/adoptions from ``room`` that another room already owns.

        Two rooms adopting the same provider area would put every device in it
        on screen twice, and switching one copy would leave the other showing
        the old state.
        """
        taken_devices = {
            device for other in layout.rooms if other.id != room.id for device in other.devices
        }
        taken_provider = {
            pid for other in layout.rooms if other.id != room.id for pid in other.provider_rooms
        }
        return replace(
            room,
            devices=tuple(d for d in room.devices if d not in taken_devices),
            provider_rooms=tuple(p for p in room.provider_rooms if p not in taken_provider),
        )

    @staticmethod
    def _release(room: RoomLayout, winner: RoomLayout) -> RoomLayout:
        """Remove from ``room`` whatever ``winner`` has just claimed."""
        if room.id == winner.id:
            return room
        devices = tuple(d for d in room.devices if d not in winner.devices)
        provider_rooms = tuple(p for p in room.provider_rooms if p not in winner.provider_rooms)
        if devices == room.devices and provider_rooms == room.provider_rooms:
            return room
        return replace(
            room,
            devices=devices,
            provider_rooms=provider_rooms,
            favorites=tuple(f for f in room.favorites if f not in winner.devices),
        )


# ----------------------------------------------------------------------
# Resolution — layout + live devices → what the screen shows
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedRoom:
    """One room with its live devices attached, ready for the wire."""

    layout: RoomLayout
    devices: tuple[Device, ...]

    @property
    def active_count(self) -> int:
        return sum(1 for d in self.devices if d.state.get("on_off") is True)

    def _reading(self, device_id: str | None, kinds: tuple[str, ...]) -> float | None:
        """The room's headline number, chosen explicitly or found by fallback.

        Falling back matters: a freshly adopted room has no sensor chosen yet,
        and a header that stays blank until someone opens a settings dialog is
        a header nobody ever fills in.
        """
        if device_id:
            picked = next((d for d in self.devices if d.id == device_id), None)
            if picked is not None:
                value = picked.state.get("value", picked.state.get("current_temperature"))
                if isinstance(value, (int, float)):
                    return float(value)
        for device in self.devices:
            haystack = f"{device.name} {device.native_id}".casefold()
            if any(kind in haystack for kind in kinds):
                value = device.state.get("value")
                if isinstance(value, (int, float)):
                    return float(value)
        # A thermostat reports the room temperature too, and a room with heating
        # but no separate thermometer is the common case in a fitted flat.
        if "temperat" in kinds[0]:
            for device in self.devices:
                current = device.state.get("current_temperature")
                if isinstance(current, (int, float)):
                    return float(current)
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            **self.layout.to_json(),
            "device_ids": [d.id for d in self.devices],
            "device_count": len(self.devices),
            "active_count": self.active_count,
            "temperature": self._reading(
                self.layout.temperature_device, ("temperat", "thermo", "°c")
            ),
            "humidity": self._reading(self.layout.humidity_device, ("humid", "feucht")),
        }


def resolve(
    layout: HomeLayout,
    devices: list[Device],
) -> tuple[list[ResolvedRoom], list[Device]]:
    """Attach live devices to rooms. Returns ``(rooms, unassigned)``.

    Precedence, highest first: an exclusion, then a hand-pinned device, then an
    adopted provider room. Anything unmatched comes back in ``unassigned`` so
    the UI can offer it rather than lose it.
    """
    by_id = {device.id: device for device in devices}
    consumed: set[str] = set()
    resolved: list[ResolvedRoom] = []

    # Pass 1: hand-pinned devices. Explicit beats inherited, so these are
    # claimed before any adoption gets a chance to sweep them elsewhere.
    pinned: dict[str, list[Device]] = {}
    for room in layout.ordered():
        bucket: list[Device] = []
        for device_id in room.devices:
            device = by_id.get(device_id)
            if device is not None and device_id not in consumed:
                bucket.append(device)
                consumed.add(device_id)
        pinned[room.id] = bucket

    # Pass 2: adoption, skipping anything already pinned or explicitly excluded.
    for room in layout.ordered():
        bucket = list(pinned.get(room.id, ()))
        if room.provider_rooms:
            adopted = set(room.provider_rooms)
            for device in devices:
                if device.id in consumed or device.id in room.excluded_devices:
                    continue
                if device.room_id in adopted:
                    bucket.append(device)
                    consumed.add(device.id)
        bucket.sort(key=lambda d: (d.id not in room.favorites, d.name.casefold()))
        resolved.append(ResolvedRoom(layout=room, devices=tuple(bucket)))

    unassigned = [device for device in devices if device.id not in consumed]
    unassigned.sort(key=lambda d: ((d.room or "￿"), d.name.casefold()))
    return resolved, unassigned


def suggestions(layout: HomeLayout, provider_rooms: list[Room]) -> list[Room]:
    """Provider areas no room has adopted yet — offered, never taken silently."""
    claimed = {pid for room in layout.rooms for pid in room.provider_rooms}
    return [room for room in provider_rooms if room.id not in claimed]


#: Words that hint at a room's purpose, mapped to an icon slug. Matching is
#: substring-based and MULTILINGUAL by necessity: a hub is set up in its owner's
#: language, so the same place arrives as "Wohnzimmer", "Living room", "Salón"
#: or "Soggiorno". These are recognition needles for user data, not UI copy —
#: hence the i18n-allow markers on the non-English entries.
_ICON_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("wohnzimmer", "living", "lounge", "salon", "sala"), "living_room"),  # i18n-allow
    (("küche", "kueche", "kitchen", "cocina", "cucina"), "kitchen"),  # i18n-allow
    (("schlaf", "bedroom", "dormitorio", "camera"), "bedroom"),  # i18n-allow
    (("bad", "bath", "wc", "dusche", "baño", "bano"), "bathroom"),  # i18n-allow
    (("büro", "buero", "office", "arbeit", "study", "oficina"), "office"),  # i18n-allow
    (("ess", "dining", "comedor"), "dining"),
    (("flur", "diele", "hall", "corridor", "entrance", "eingang"), "hallway"),
    (("kind", "kids", "nursery", "child"), "kids"),
    (("garage", "carport"), "garage"),
    (("garten", "garden", "terrasse", "terrace", "patio", "jardin"), "garden"),
    (("keller", "basement", "cellar", "sotano"), "basement"),
    (("dach", "attic", "loft", "speicher"), "attic"),
    (("wasch", "laundry", "utility", "hauswirtschaft"), "laundry"),
    (("balkon", "balcony"), "balcony"),
    (("treppe", "stair", "escalera"), "stairs"),
)


def suggest_icon(name: str) -> str:
    """Best-effort icon for a room name. Falls back to the neutral glyph."""
    haystack = (name or "").casefold()
    for needles, icon in _ICON_HINTS:
        if any(needle in haystack for needle in needles):
            return icon
    return "room"


def suggest_color(name: str) -> str:
    """A stable colour per room name.

    Hashing the NAME rather than picking at random means the same room keeps
    its colour across machines and re-imports — a room that changed colour on
    every restart would be a room nobody learns to recognise at a glance.
    """
    if not name:
        return "slate"
    total = sum(ord(char) for char in name.casefold())
    # Skip "slate": it is the neutral default, reserved for rooms with no hint.
    palette = ROOM_COLORS[1:]
    return palette[total % len(palette)]


#: Module-level default, mirroring how the registry is held once per process.
_STORE: HomeLayoutStore | None = None


def get_store() -> HomeLayoutStore:
    global _STORE
    if _STORE is None:
        _STORE = HomeLayoutStore()
    return _STORE


def reset_store(store: HomeLayoutStore | None = None) -> None:
    """Swap the process-wide store. Used by the routes' tests."""
    global _STORE
    _STORE = store


__all__ = [
    "HomeLayout",
    "HomeLayoutStore",
    "LAYOUT_VERSION",
    "ROOM_COLORS",
    "ROOM_ICONS",
    "ResolvedRoom",
    "RoomLayout",
    "get_store",
    "reset_store",
    "resolve",
    "suggest_color",
    "suggest_icon",
    "suggestions",
]
