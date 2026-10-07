"""The just-in-time permission events: vocabulary parity, wire shape, bus safety.

``PermissionNeeded`` / ``PermissionResolved`` carry values that cross Python
(the permission service), the bus, the ``/ws`` forwarder and the UI copy keyed
per (feature, reason) — the five-layer shape AP-4 / BUG-008 guards. A drift
shows up here as one failing test instead of an unknown-reason blank card.

1. ``jarvis/core/events.py``                       — the tuples + dataclasses (source of truth)
2. ``jarvis/ui/web/schema.py``                     — ``event_to_ws_envelope`` (the /ws wire shape)
3. ``jarvis/ui/web/frontend/src/lib/permissionEvents.ts`` — the TS const tuples + payload interfaces
4. ``jarvis/tasks/event_catalog.py``               — routines must NOT be able to trigger on them
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from jarvis.core import events as events_mod
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    PERMISSION_FEATURES,
    PERMISSION_NEEDED_ORIGINS,
    PERMISSION_NEEDED_PHASES,
    PERMISSION_NEEDED_REASONS,
    DictationRefused,
    Event,
    PermissionNeeded,
    PermissionResolved,
)
from jarvis.ui.web.schema import event_to_ws_envelope

REPO_ROOT = Path(__file__).resolve().parents[3]
TS_FILE = REPO_ROOT / "jarvis/ui/web/frontend/src/lib/permissionEvents.ts"

_ENVELOPE_FIELDS = {"trace_id", "timestamp_ns", "source_layer"}

# Python tuple -> (TS const tuple name, TS union type name)
_VOCABULARIES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "features": (PERMISSION_FEATURES, "PERMISSION_FEATURES", "PermissionFeature"),
    "reasons": (
        PERMISSION_NEEDED_REASONS,
        "PERMISSION_NEEDED_REASONS",
        "PermissionNeededReason",
    ),
    "phases": (PERMISSION_NEEDED_PHASES, "PERMISSION_NEEDED_PHASES", "PermissionNeededPhase"),
    "origins": (PERMISSION_NEEDED_ORIGINS, "PERMISSION_NEEDED_ORIGINS", "PermissionNeededOrigin"),
}

# Event field -> the TS union its payload interface must use for it.
_ENUMERATED_FIELDS: dict[str, str] = {
    "feature": "PermissionFeature",
    "reason": "PermissionNeededReason",
    "phase": "PermissionNeededPhase",
    "origin": "PermissionNeededOrigin",
}

# Python annotation (a string: events.py uses `from __future__ import annotations`)
# -> the TS type the twin must declare. An unmapped annotation fails loudly so a
# new field type forces this table to be extended instead of passing unchecked.
_TS_TYPES: dict[str, str] = {
    "str": "string",
    "bool": "boolean",
    "tuple[str, ...]": "string[]",
}


def _ts_source() -> str:
    return TS_FILE.read_text(encoding="utf-8")


def _ts_tuple(name: str) -> list[str]:
    match = re.search(rf"export const {name} = \[([^\]]*)\] as const;", _ts_source())
    assert match, f"{name} const tuple missing from {TS_FILE.name}"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def _ts_interface(name: str) -> dict[str, str]:
    """Field name -> declared TS type of one exported interface (declarations only)."""
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", _ts_source(), re.S)
    assert match, f"interface {name} missing from {TS_FILE.name}"
    return dict(re.findall(r"^\s{2}([a-z_]+): ([^;\n]+);", match.group(1), re.M))


def _payload_fields(event_cls: type[Event]) -> dict[str, str]:
    return {f.name: str(f.type) for f in fields(event_cls) if f.name not in _ENVELOPE_FIELDS}


def _needed(**overrides: object) -> PermissionNeeded:
    values: dict[str, object] = {
        "permissions": ("accessibility",),
        "feature": "computer_use",
        "reason": "needs_settings",
        "phase": "blocked",
        "origin": "user",
        "can_prompt": True,
        "can_open_settings": True,
        "detail": "Accessibility is off, so Jarvis cannot click or type for you yet.",
        "source_layer": "platform.permissions",
    }
    values.update(overrides)
    return PermissionNeeded(**values)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Vocabulary: Python tuples <-> TypeScript twin (AP-4)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("key", list(_VOCABULARIES))
def test_python_vocabulary_is_unique_snake_case(key: str) -> None:
    values = _VOCABULARIES[key][0]
    assert values, key
    assert len(values) == len(set(values)), f"duplicate value in the {key} tuple"
    assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", v) for v in values), key


@pytest.mark.parametrize("key", list(_VOCABULARIES))
def test_typescript_tuple_matches_python(key: str) -> None:
    py_values, ts_name, _ = _VOCABULARIES[key]
    ts_values = _ts_tuple(ts_name)
    assert len(ts_values) == len(set(ts_values)), f"duplicate value in {ts_name}"
    assert set(ts_values) == set(py_values), (
        f"{ts_name} drift: extra={set(ts_values) - set(py_values)}, "
        f"missing={set(py_values) - set(ts_values)}"
    )


@pytest.mark.parametrize("key", list(_VOCABULARIES))
def test_typescript_union_is_derived_from_the_tuple(key: str) -> None:
    _, ts_name, union_name = _VOCABULARIES[key]
    assert f"export type {union_name} = (typeof {ts_name})[number];" in _ts_source()


@pytest.mark.parametrize(
    ("event_cls", "interface"),
    [
        (PermissionNeeded, "PermissionNeededPayload"),
        (PermissionResolved, "PermissionResolvedPayload"),
    ],
)
def test_typescript_payload_interface_matches_the_event(
    event_cls: type[Event], interface: str
) -> None:
    py_fields = _payload_fields(event_cls)
    ts_fields = _ts_interface(interface)
    assert set(ts_fields) == set(py_fields), (
        f"{interface} drift: extra={set(ts_fields) - set(py_fields)}, "
        f"missing={set(py_fields) - set(ts_fields)}"
    )
    for name, annotation in py_fields.items():
        expected = _ENUMERATED_FIELDS.get(name) or _TS_TYPES.get(annotation)
        assert expected is not None, (
            f"no TS type mapped for {event_cls.__name__}.{name}: {annotation}"
        )
        assert ts_fields[name] == expected, (
            f"{interface}.{name} is {ts_fields[name]!r}, expected {expected!r}"
        )


# ----------------------------------------------------------------------
# Event shape: frozen, slotted, conventions of the neighbouring events
# ----------------------------------------------------------------------


@pytest.mark.parametrize("event_cls", [PermissionNeeded, PermissionResolved])
def test_permission_events_follow_the_event_conventions(event_cls: type[Event]) -> None:
    assert issubclass(event_cls, Event)
    assert is_dataclass(event_cls)
    event = event_cls()
    # Base-class envelope fields come from Event's own default factories.
    assert isinstance(event.trace_id, UUID)
    assert event.timestamp_ns > 0
    assert event.source_layer == ""
    assert event_cls().trace_id != event.trace_id
    # slots=True: no per-instance dict to grow state on.
    assert not hasattr(event, "__dict__")
    # An explicit trace id is carried as given.
    trace = uuid4()
    assert event_cls(trace_id=trace).trace_id == trace


def test_permission_needed_defaults_are_the_inert_values() -> None:
    event = PermissionNeeded()
    assert event.permissions == ()
    assert (event.feature, event.reason, event.phase, event.origin) == ("", "", "", "")
    assert event.target == ""
    assert (event.can_prompt, event.can_open_settings, event.outside_app) == (False, False, False)
    assert event.detail == ""


def test_permission_resolved_defaults_are_the_inert_values() -> None:
    event = PermissionResolved()
    assert event.permissions == ()
    assert event.feature == ""
    assert event.granted is False


@pytest.mark.parametrize(
    ("event", "attribute", "value"),
    [
        (_needed(), "reason", "denied"),
        (_needed(), "permissions", ()),
        (_needed(), "can_prompt", False),
        (PermissionResolved(feature="voice", granted=False), "granted", True),
    ],
)
def test_permission_events_are_frozen(event: Event, attribute: str, value: object) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(event, attribute, value)


def test_permissions_are_a_tuple_so_the_event_stays_immutable_and_hashable() -> None:
    needed = _needed(permissions=("accessibility", "screen_recording"))
    resolved = PermissionResolved(permissions=("microphone",), feature="voice", granted=True)
    assert isinstance(needed.permissions, tuple)
    assert isinstance(resolved.permissions, tuple)
    # A list field would make the frozen event unhashable (and mutable in place).
    assert isinstance(hash(needed), int)
    assert isinstance(hash(resolved), int)


def test_dictation_refused_keeps_its_shape() -> None:
    """The microphone case publishes BOTH events; the old one is not reshaped."""
    assert [f.name for f in fields(DictationRefused)] == [
        "trace_id",
        "timestamp_ns",
        "source_layer",
        "reason",
        "detail",
    ]
    assert not issubclass(PermissionNeeded, DictationRefused)


# ----------------------------------------------------------------------
# Wire shape: the /ws forwarder
# ----------------------------------------------------------------------


def test_permission_needed_round_trips_through_the_ws_serialisation() -> None:
    trace = uuid4()
    event = _needed(
        permissions=("accessibility", "screen_recording"),
        trace_id=trace,
        target="com.apple.Music",
        outside_app=True,
    )

    envelope = event_to_ws_envelope(event)
    # Starlette's WebSocket.send_json encodes exactly like this.
    wire = json.loads(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False))

    assert wire["type"] == "event"
    assert wire["event_name"] == "PermissionNeeded"
    assert wire["trace_id"] == str(trace)
    assert wire["source_layer"] == "platform.permissions"
    assert isinstance(wire["timestamp_ns"], int)
    # Envelope-level fields are lifted out of the payload, not repeated in it.
    assert not _ENVELOPE_FIELDS & set(wire["payload"])
    assert wire["payload"] == {
        "permissions": ["accessibility", "screen_recording"],
        "feature": "computer_use",
        "reason": "needs_settings",
        "phase": "blocked",
        "origin": "user",
        "target": "com.apple.Music",
        "can_prompt": True,
        "can_open_settings": True,
        "outside_app": True,
        "detail": "Accessibility is off, so Jarvis cannot click or type for you yet.",
    }
    # Tuples arrive as JSON arrays, and the order of the episode is kept.
    assert isinstance(wire["payload"]["permissions"], list)


def test_permission_resolved_round_trips_through_the_ws_serialisation() -> None:
    event = PermissionResolved(permissions=("microphone",), feature="voice", granted=True)

    wire = json.loads(json.dumps(event_to_ws_envelope(event), ensure_ascii=False))

    assert wire["event_name"] == "PermissionResolved"
    assert wire["payload"] == {"permissions": ["microphone"], "feature": "voice", "granted": True}


def test_every_ws_payload_field_is_declared_by_the_typescript_twin() -> None:
    """The envelope payload keys are exactly what the TS interface declares."""
    for event, interface in (
        (_needed(), "PermissionNeededPayload"),
        (PermissionResolved(), "PermissionResolvedPayload"),
    ):
        payload = event_to_ws_envelope(event)["payload"]
        assert set(payload) == set(_ts_interface(interface)), interface


def test_permission_events_reach_a_ws_client_through_the_real_forwarder() -> None:
    """Published on the bus, the events arrive as envelopes — no per-event wiring.

    Uses the real ``WebServer`` ``/ws`` route and its wildcard forwarder, the
    same way ``tests/integration/test_websocket_echo.py`` does.
    """
    from fastapi.testclient import TestClient

    from jarvis.core.config import JarvisConfig
    from jarvis.ui.web.server import WebServer

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    server = WebServer(cfg, bus=EventBus())

    async def _publish() -> None:
        await server.bus.publish(_needed(permissions=("microphone",), feature="voice"))
        await server.bus.publish(
            PermissionResolved(permissions=("microphone",), feature="voice", granted=True)
        )

    with TestClient(server.app) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "welcome"

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_publish())
        finally:
            loop.close()

        needed = ws.receive_json()
        resolved = ws.receive_json()

    assert needed["type"] == "event"
    assert needed["event_name"] == "PermissionNeeded"
    assert needed["payload"]["permissions"] == ["microphone"]
    assert needed["payload"]["phase"] == "blocked"
    assert resolved["event_name"] == "PermissionResolved"
    assert resolved["payload"]["granted"] is True


# ----------------------------------------------------------------------
# Bus safety (AP-18)
# ----------------------------------------------------------------------


async def test_raising_resolved_subscriber_does_not_stop_later_subscribers() -> None:
    from loguru import logger

    bus = EventBus()
    calls: list[str] = []

    async def before(event: Event) -> None:
        calls.append("before")

    async def boom(event: Event) -> None:
        calls.append("boom")
        raise RuntimeError("subscriber blew up")

    async def after(event: Event) -> None:
        calls.append("after")

    bus.subscribe(PermissionResolved, before)
    bus.subscribe(PermissionResolved, boom)
    bus.subscribe(PermissionResolved, after)

    failures: list[str] = []
    sink = logger.add(
        lambda message: failures.append(message.record["extra"].get("event", "")),
        level="WARNING",
    )
    try:
        # The exception must never leave EventBus._safe_dispatch.
        await bus.publish(PermissionResolved(permissions=("microphone",), feature="voice"))
    finally:
        logger.remove(sink)

    assert sorted(calls) == ["after", "before", "boom"]
    assert "PermissionResolved" in failures, "the failing subscriber was not logged"


async def test_raising_wildcard_subscriber_does_not_stop_permission_delivery() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def boom(event: Event) -> None:
        raise RuntimeError("observer blew up")

    async def typed(event: PermissionNeeded) -> None:
        seen.append(f"typed:{event.feature}")

    async def observer(event: Event) -> None:
        seen.append(f"observer:{type(event).__name__}")

    bus.subscribe_all(boom)
    bus.subscribe(PermissionNeeded, typed)
    bus.subscribe_all(observer)

    await bus.publish(_needed(feature="dictation"))
    await bus.publish(_needed(feature="voice"))  # the bus is still usable afterwards

    assert sorted(seen) == [
        "observer:PermissionNeeded",
        "observer:PermissionNeeded",
        "typed:dictation",
        "typed:voice",
    ]


# ----------------------------------------------------------------------
# Routine triggers: the permission events are excluded from the catalog
# ----------------------------------------------------------------------


def _core_event_names() -> set[str]:
    return {
        name
        for name, obj in inspect.getmembers(events_mod, inspect.isclass)
        if obj is not Event and issubclass(obj, Event)
    }


def test_the_catalog_exclusion_is_exactly_the_two_permission_events() -> None:
    from jarvis.tasks.event_catalog import EXCLUDED_EVENT_NAMES

    assert frozenset({"PermissionNeeded", "PermissionResolved"}) == EXCLUDED_EVENT_NAMES
    # The exclusion names real events: a rename must not leave it inert.
    assert EXCLUDED_EVENT_NAMES <= _core_event_names()


def test_event_catalog_omits_the_permission_events_and_nothing_else() -> None:
    from jarvis.tasks.event_catalog import EXCLUDED_EVENT_NAMES, event_catalog

    catalog = event_catalog()

    assert not EXCLUDED_EVENT_NAMES & set(catalog)
    # Every other event of jarvis.core.events is still offered to routines.
    assert _core_event_names() - EXCLUDED_EVENT_NAMES <= set(catalog)
    assert "DictationRefused" in catalog


@pytest.mark.parametrize("name", ["PermissionNeeded", "PermissionResolved"])
@pytest.mark.parametrize("filter_expr", [None, "feature == 'voice'"])
def test_routines_cannot_be_scheduled_on_the_permission_events(
    name: str, filter_expr: str | None
) -> None:
    from jarvis.tasks.event_catalog import validate_event_schedule

    schedule: dict[str, object] = {"event_name": name}
    if filter_expr:
        schedule["filter_expr"] = filter_expr
    with pytest.raises(ValueError, match="cannot trigger a routine"):
        validate_event_schedule(schedule)


def test_a_neighbouring_event_stays_schedulable() -> None:
    from jarvis.tasks.event_catalog import validate_event_schedule

    validate_event_schedule({"event_name": "DictationRefused", "filter_expr": "reason == 'no_stt'"})
