"""Contract tests for the shared frozen event vocabulary.

These events are the ONLY hard dependency shared across the three parallel
sub-agent modules (bus/router, tools/worker, oops). Mirrors the design of the
production `jarvis/core/events.py`: frozen dataclasses carrying `trace_id`
(correlation) + `timestamp_ns` (latency spans).
"""
from __future__ import annotations

import dataclasses
import uuid

import pytest

from optimistic.events import (
    AckEmitted,
    CorrectionReason,
    DumbToolFired,
    Event,
    MissionSpawn,
    RouteKind,
    UserUtterance,
    WorkerCompleted,
    WorkerCorrectionNeeded,
    WorkerStarted,
)


def test_events_are_frozen() -> None:
    ev = AckEmitted(text="Geht klar")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.text = "changed"  # type: ignore[misc]


def test_events_autostamp_trace_and_time() -> None:
    ev = UserUtterance(text="hallo")
    assert isinstance(ev.trace_id, uuid.UUID)
    assert isinstance(ev.timestamp_ns, int)
    assert ev.timestamp_ns > 0


def test_trace_id_propagates_when_passed() -> None:
    u = UserUtterance(text="schreib Max eine Mail")
    ack = AckEmitted(text="Geht klar", trace_id=u.trace_id)
    assert ack.trace_id == u.trace_id


def test_all_events_subclass_event() -> None:
    for cls in (
        UserUtterance,
        AckEmitted,
        MissionSpawn,
        WorkerStarted,
        WorkerCompleted,
        WorkerCorrectionNeeded,
        DumbToolFired,
    ):
        assert issubclass(cls, Event)


def test_route_kind_values() -> None:
    assert RouteKind.SMALLTALK.value == "smalltalk"
    assert RouteKind.DUMB_TOOL.value == "dumb_tool"
    assert RouteKind.SMART_TOOL.value == "smart_tool"


def test_correction_reason_is_wire_vocab() -> None:
    # Five-layer enum single source of truth (prototype scope: the Python enum).
    assert CorrectionReason.MISSING_INFO.value == "missing_info"
    assert {r.value for r in CorrectionReason} >= {
        "missing_info",
        "auth_required",
        "network_error",
        "fatal",
    }


def test_mission_spawn_carries_context_package() -> None:
    # The "silent context package": transcript + the actual command.
    ev = MissionSpawn(
        command="schreib Max eine Mail",
        context={"transcript": ["earlier turn"]},
        tool_name="gmail",
    )
    assert ev.command.startswith("schreib")
    assert "transcript" in ev.context
    assert ev.mission_id  # auto-generated, non-empty


def test_correction_event_carries_reason_enum() -> None:
    ev = WorkerCorrectionNeeded(
        mission_id="m1",
        reason=CorrectionReason.MISSING_INFO,
        detail="no email address for Max",
    )
    assert ev.reason is CorrectionReason.MISSING_INFO
