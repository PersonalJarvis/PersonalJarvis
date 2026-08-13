"""Jarvis's own room layer: ownership, resolution, and the routes that edit it.

The properties pinned here are the ones that decide whether a person can trust
the section with an arrangement they made by hand:

* what they created, renamed or deleted SURVIVES the next read of the hub;
* a device shows up under exactly one room, and never vanishes entirely;
* a room can exist before any hardware does — the common case for someone
  setting the house up before the lamps arrive;
* a partial edit changes only what it names.

The restore trap is the recurring failure mode this file guards: an import that
re-runs on every read quietly resurrects deleted rooms and undoes renames, and
the user reads that as "my settings don't save".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.smarthome.home_layout import (
    HomeLayout,
    HomeLayoutStore,
    ResolvedRoom,
    RoomLayout,
    reset_store,
    resolve,
    suggest_color,
    suggest_icon,
    suggestions,
)
from jarvis.smarthome.models import Capability, Device, DeviceKind, Room
from jarvis.smarthome.providers.demo import DemoProvider
from jarvis.smarthome.registry import SmartHomeRegistry
from jarvis.ui.web import smarthome_routes


@pytest.fixture
def store(tmp_path: Path) -> HomeLayoutStore:
    return HomeLayoutStore(path=tmp_path / "home.json")


def _device(device_id: str, *, name: str = "Lamp", room_id: str | None = None) -> Device:
    provider, _, native = device_id.partition(":")
    return Device(
        id=device_id,
        provider=provider,
        native_id=native,
        name=name,
        kind=DeviceKind.LIGHT,
        capabilities=(Capability.ON_OFF,),
        state={"on_off": False},
        room_id=room_id,
    )


# ----------------------------------------------------------------------
# Ownership — the arrangement is the user's, not the hub's
# ----------------------------------------------------------------------


def test_a_room_can_exist_before_any_hardware(store: HomeLayoutStore) -> None:
    """Someone planning the house before the lamps arrive is the common case."""
    room = store.create_room(name="Wohnzimmer", icon="living_room", color="amber")
    assert room.id == "wohnzimmer"
    assert store.load().get("wohnzimmer") is not None


def test_umlauts_survive_as_a_readable_id(store: HomeLayoutStore) -> None:
    """A slug that collapsed 'Küche' to 'k-che' would be unreadable in a URL."""
    assert store.create_room(name="Küche").id == "kueche"
    assert store.create_room(name="Büro").id == "buero"


def test_duplicate_room_names_are_refused_with_a_sentence(store: HomeLayoutStore) -> None:
    store.create_room(name="Küche")
    with pytest.raises(ValueError, match="already a room"):
        store.create_room(name="küche")


def test_adopt_is_the_explicit_import_button(store: HomeLayoutStore) -> None:
    """``adopt`` always takes free areas — it is the button, not the reflex.

    The once-only guarantee lives one level up, in the route: it adopts solely
    when no layout file exists yet (see the route test below). Keeping the two
    apart is what lets "import my hub's areas" stay available after a delete
    without a plain page load resurrecting anything.
    """
    areas = [
        Room(id="ha:kitchen", name="Kitchen", provider="ha"),
        Room(id="ha:bath", name="Bathroom", provider="ha"),
    ]
    store.adopt(areas)
    assert {r.id for r in store.load().rooms} == {"kitchen", "bathroom"}

    store.delete_room("bathroom")
    store.adopt(areas)  # deliberate re-import: the freed area comes back
    assert {r.id for r in store.load().rooms} == {"kitchen", "bathroom"}


def test_adopting_twice_creates_no_duplicates(store: HomeLayoutStore) -> None:
    areas = [Room(id="ha:kitchen", name="Kitchen", provider="ha")]
    store.adopt(areas)
    store.adopt(areas)
    assert [r.id for r in store.load().rooms] == ["kitchen"]


def test_a_rename_survives_a_re_import(store: HomeLayoutStore) -> None:
    areas = [Room(id="ha:kitchen", name="Kitchen", provider="ha")]
    store.adopt(areas)
    store.update_room("kitchen", name="Küche")
    store.adopt(areas)
    rooms = store.load().rooms
    assert [r.name for r in rooms] == ["Küche"]
    assert rooms[0].provider_rooms == ("ha:kitchen",)


def test_two_platforms_naming_the_same_place_merge_into_one_room(
    store: HomeLayoutStore,
) -> None:
    """The merge the old mirror-only design could not express at all."""
    store.adopt(
        [
            Room(id="ha:living", name="Living room", provider="ha"),
            Room(id="hue:1", name="Living room", provider="hue"),
        ]
    )
    rooms = store.load().rooms
    assert len(rooms) == 1
    assert set(rooms[0].provider_rooms) == {"ha:living", "hue:1"}


def test_a_new_area_is_suggested_not_adopted(store: HomeLayoutStore) -> None:
    store.adopt([Room(id="ha:kitchen", name="Kitchen", provider="ha")])
    layout = store.load()
    fresh = Room(id="ha:garage", name="Garage", provider="ha")
    assert [r.id for r in suggestions(layout, [fresh])] == ["ha:garage"]


def test_a_partial_update_leaves_everything_else_alone(store: HomeLayoutStore) -> None:
    """Sending only a name must not wipe the colour, the areas or the favourites."""
    store.create_room(name="Küche", icon="kitchen", color="amber", floor="Ground")
    store.assign_devices("kueche", ["demo:light.a"])
    store.toggle_favorite("kueche", "demo:light.a")

    updated = store.update_room("kueche", name="Kochnische")
    assert updated is not None
    assert updated.name == "Kochnische"
    assert updated.icon == "kitchen"
    assert updated.color == "amber"
    assert updated.floor == "Ground"
    assert updated.favorites == ("demo:light.a",)


def test_deleting_a_room_keeps_its_devices_switchable(store: HomeLayoutStore) -> None:
    """A device that disappeared with its room is a device nobody can turn off."""
    store.create_room(name="Küche")
    store.assign_devices("kueche", ["demo:light.a"])
    store.delete_room("kueche")

    rooms, unassigned = resolve(store.load(), [_device("demo:light.a")])
    assert rooms == []
    assert [d.id for d in unassigned] == ["demo:light.a"]


def test_reorder_puts_unlisted_rooms_at_the_end(store: HomeLayoutStore) -> None:
    for name in ("A", "B", "C"):
        store.create_room(name=name)
    store.reorder(["c", "a"])
    assert [r.name for r in store.load().ordered()] == ["C", "A", "B"]


# ----------------------------------------------------------------------
# Resolution — one device, one room, nothing lost
# ----------------------------------------------------------------------


def test_a_pinned_device_beats_an_adopted_one(store: HomeLayoutStore) -> None:
    """Moving a lamp by hand must win over what the hub's area says."""
    store.adopt([Room(id="ha:kitchen", name="Kitchen", provider="ha")])
    store.create_room(name="Office")
    store.assign_devices("office", ["ha:light.x"])

    device = _device("ha:light.x", room_id="ha:kitchen")
    rooms, unassigned = resolve(store.load(), [device])
    placed = {r.layout.name: [d.id for d in r.devices] for r in rooms}
    assert placed == {"Kitchen": [], "Office": ["ha:light.x"]}
    assert unassigned == []


def test_a_device_removed_from_a_room_does_not_come_back(store: HomeLayoutStore) -> None:
    """Un-pinning alone is not enough — the adoption would sweep it right back."""
    store.adopt([Room(id="ha:kitchen", name="Kitchen", provider="ha")])
    device = _device("ha:light.x", room_id="ha:kitchen")

    rooms, _ = resolve(store.load(), [device])
    assert [d.id for d in rooms[0].devices] == ["ha:light.x"]

    store.remove_device("kitchen", "ha:light.x")
    rooms, unassigned = resolve(store.load(), [device])
    assert rooms[0].devices == ()
    assert [d.id for d in unassigned] == ["ha:light.x"]


def test_pinning_a_device_takes_it_out_of_its_previous_room(
    store: HomeLayoutStore,
) -> None:
    """Two rooms showing the same lamp would disagree the moment one is used."""
    store.create_room(name="A")
    store.create_room(name="B")
    store.assign_devices("a", ["demo:light.x"])
    store.assign_devices("b", ["demo:light.x"])

    rooms, _ = resolve(store.load(), [_device("demo:light.x")])
    holders = [r.layout.name for r in rooms if r.devices]
    assert holders == ["B"]


def test_two_rooms_cannot_adopt_the_same_area(store: HomeLayoutStore) -> None:
    store.create_room(name="A", provider_rooms=["ha:kitchen"])
    store.create_room(name="B", provider_rooms=["ha:kitchen"])
    layout = store.load()
    assert layout.get("a") is not None
    assert layout.get("a").provider_rooms == ("ha:kitchen",)  # type: ignore[union-attr]
    assert layout.get("b").provider_rooms == ()  # type: ignore[union-attr]


def test_favorites_sort_to_the_front(store: HomeLayoutStore) -> None:
    store.create_room(name="Küche")
    store.assign_devices("kueche", ["demo:light.b", "demo:light.a"])
    store.toggle_favorite("kueche", "demo:light.b")

    rooms, _ = resolve(
        store.load(),
        [_device("demo:light.a", name="Alpha"), _device("demo:light.b", name="Bravo")],
    )
    assert [d.id for d in rooms[0].devices] == ["demo:light.b", "demo:light.a"]


def test_room_header_falls_back_to_any_thermometer(store: HomeLayoutStore) -> None:
    """A header that stays blank until a settings dialog is one nobody fills in."""
    sensor = Device(
        id="demo:sensor.t",
        provider="demo",
        native_id="sensor.t",
        name="Room temperature",
        kind=DeviceKind.SENSOR,
        capabilities=(Capability.READ_ONLY,),
        state={"value": 21.5},
        unit="°C",
    )
    room = ResolvedRoom(layout=RoomLayout(id="k", name="Küche"), devices=(sensor,))
    assert room.to_json()["temperature"] == 21.5


# ----------------------------------------------------------------------
# Storage — a damaged file must not blank the house
# ----------------------------------------------------------------------


def test_a_corrupt_layout_file_yields_an_empty_house_not_a_crash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home.json"
    path.write_text("{not json at all", encoding="utf-8")
    assert HomeLayoutStore(path=path).load().rooms == ()


def test_a_single_damaged_room_does_not_drop_the_others(tmp_path: Path) -> None:
    path = tmp_path / "home.json"
    path.write_text(
        '{"version": 1, "rooms": [{"id": "", "name": "broken"}, '
        '{"id": "kueche", "name": "Küche"}]}',
        encoding="utf-8",
    )
    rooms = HomeLayoutStore(path=path).load().rooms
    assert [r.name for r in rooms] == ["Küche"]


def test_the_layout_round_trips_through_disk(store: HomeLayoutStore) -> None:
    store.create_room(name="Küche", icon="kitchen", color="amber", floor="Erdgeschoss")
    store.assign_devices("kueche", ["demo:light.a"])
    reloaded = HomeLayoutStore(path=store.path).load().get("kueche")
    assert reloaded is not None
    assert (reloaded.name, reloaded.icon, reloaded.color) == ("Küche", "kitchen", "amber")
    assert reloaded.floor == "Erdgeschoss"
    assert reloaded.devices == ("demo:light.a",)


def test_icon_and_colour_hints_are_multilingual_and_stable() -> None:
    """A hub speaks whatever language its owner set up."""
    assert suggest_icon("Wohnzimmer") == suggest_icon("Living room") == "living_room"
    assert suggest_icon("Küche") == suggest_icon("Kitchen") == "kitchen"
    assert suggest_icon("Rumpelkammer") == "room"
    # Stable across calls: a room that changed colour on every restart is one
    # nobody learns to recognise at a glance.
    assert suggest_color("Küche") == suggest_color("Küche")


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
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


def test_first_read_lands_on_a_furnished_house(client: TestClient) -> None:
    """A connected hub must not present an empty screen and a setup chore."""
    body = client.get("/api/smarthome/rooms").json()
    assert body["rooms"], "the demo home's areas should have been adopted"
    assert body["icons"] and body["colors"], "the editor's vocabulary travels with the data"
    assert all(room["device_count"] >= 0 for room in body["rooms"])


def test_reloading_never_resurrects_a_deleted_room(client: TestClient) -> None:
    """The restore trap, at the level where it would actually bite.

    Adoption runs ONLY when no layout exists yet. If a plain read re-imported,
    a deleted room would reappear on the next refresh and the user would read
    that as "my settings don't save".
    """
    first = client.get("/api/smarthome/rooms").json()["rooms"]
    victim = first[0]["id"]
    client.delete(f"/api/smarthome/rooms/{victim}")

    for _ in range(3):
        client.get("/api/smarthome/rooms")
        client.get("/api/smarthome/overview?refresh=true")
    surviving = {r["id"] for r in client.get("/api/smarthome/rooms").json()["rooms"]}
    assert victim not in surviving


def test_a_room_survives_the_next_overview(client: TestClient) -> None:
    created = client.post("/api/smarthome/rooms", json={"name": "Dachboden"})
    assert created.status_code == 201
    names = [r["name"] for r in client.get("/api/smarthome/overview").json()["rooms"]]
    assert "Dachboden" in names


def test_overview_and_rooms_agree(client: TestClient) -> None:
    """Two screens disagreeing about the house is what stops people trusting it."""
    from_overview = client.get("/api/smarthome/overview").json()["rooms"]
    from_rooms = client.get("/api/smarthome/rooms").json()["rooms"]
    assert [r["id"] for r in from_overview] == [r["id"] for r in from_rooms]


def test_renaming_a_room_is_a_patch_that_keeps_its_devices(client: TestClient) -> None:
    room = client.get("/api/smarthome/rooms").json()["rooms"][0]
    res = client.patch(f"/api/smarthome/rooms/{room['id']}", json={"name": "Neuer Name"})
    assert res.status_code == 200
    after = next(r for r in res.json()["rooms"] if r["id"] == room["id"])
    assert after["name"] == "Neuer Name"
    assert after["device_ids"] == room["device_ids"]


def test_a_duplicate_name_is_a_400_the_user_can_read(client: TestClient) -> None:
    client.post("/api/smarthome/rooms", json={"name": "Atelier"})
    res = client.post("/api/smarthome/rooms", json={"name": "atelier"})
    assert res.status_code == 400
    assert "already a room" in res.json()["detail"]


def test_an_unknown_room_is_a_404(client: TestClient) -> None:
    assert client.patch("/api/smarthome/rooms/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/smarthome/rooms/nope").status_code == 404


def test_moving_a_device_between_rooms_leaves_it_in_exactly_one(
    client: TestClient,
) -> None:
    client.post("/api/smarthome/rooms", json={"name": "Werkstatt"})
    body = client.post(
        "/api/smarthome/rooms/werkstatt/devices",
        json={"device_ids": ["demo:light.kitchen"]},
    ).json()
    holders = [r["id"] for r in body["rooms"] if "demo:light.kitchen" in r["device_ids"]]
    assert holders == ["werkstatt"]


def test_a_device_taken_out_of_a_room_is_offered_not_lost(client: TestClient) -> None:
    client.post("/api/smarthome/rooms", json={"name": "Werkstatt"})
    client.post(
        "/api/smarthome/rooms/werkstatt/devices",
        json={"device_ids": ["demo:light.kitchen"]},
    )
    body = client.delete("/api/smarthome/rooms/werkstatt/devices/demo:light.kitchen").json()
    assert "demo:light.kitchen" in body["unassigned"]


def test_everything_off_switches_the_whole_room(client: TestClient) -> None:
    room = next(r for r in client.get("/api/smarthome/rooms").json()["rooms"] if r["device_count"])
    client.post("/api/smarthome/devices/demo:light.kitchen/command", json={"command": "turn_on"})
    target = next(
        r
        for r in client.get("/api/smarthome/rooms").json()["rooms"]
        if "demo:light.kitchen" in r["device_ids"]
    )
    body = client.post(
        f"/api/smarthome/rooms/{target['id']}/command", json={"command": "turn_off"}
    ).json()
    assert body["ok"] is True
    lamp = client.get("/api/smarthome/devices/demo:light.kitchen").json()
    assert lamp["state"]["on_off"] is False
    assert room["id"]  # the fixture's house really does have rooms


def test_a_room_wide_unlock_is_refused_outright(client: TestClient) -> None:
    """One tap must never be able to open every door in the house."""
    room = client.get("/api/smarthome/rooms").json()["rooms"][0]
    res = client.post(f"/api/smarthome/rooms/{room['id']}/command", json={"command": "unlock"})
    assert res.status_code == 400
    assert "one device at a time" in res.json()["detail"]
    door = client.get("/api/smarthome/devices/demo:lock.front_door").json()
    assert door["state"]["locked"] is True


def test_import_adopts_areas_that_are_still_free(client: TestClient) -> None:
    client.get("/api/smarthome/rooms")  # first-run adoption
    before = client.get("/api/smarthome/rooms").json()
    assert before["suggestions"] == []
    # Deleting a room frees its area again, which is exactly what import is for.
    client.delete(f"/api/smarthome/rooms/{before['rooms'][0]['id']}")
    freed = client.get("/api/smarthome/rooms").json()
    assert freed["suggestions"], "a freed area should be offered back"
    after = client.post("/api/smarthome/rooms/import").json()
    assert after["suggestions"] == []
    assert len(after["rooms"]) == len(before["rooms"])


def test_layout_helpers_expose_a_stable_json_shape() -> None:
    """The wire shape is what the frontend binds to; pin it deliberately."""
    layout = HomeLayout(rooms=(RoomLayout(id="k", name="Küche"),))
    assert set(layout.to_json()) == {"version", "rooms"}
    assert set(layout.to_json()["rooms"][0]) == {
        "id",
        "name",
        "icon",
        "color",
        "floor",
        "order",
        "provider_rooms",
        "devices",
        "excluded_devices",
        "favorites",
        "temperature_device",
        "humidity_device",
    }
