"""Shared fixtures for Phase-6 mission tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.missions.events import EventEnvelope, MissionDispatched, now_ms
from jarvis.missions.ids import uuid7_str


@pytest.fixture
def tmp_missions_db(tmp_path: Path) -> Path:
    """Pfad zu einer frischen, leeren missions.db im tmp-Verzeichnis."""
    return tmp_path / "missions.db"


@pytest.fixture
def fake_mission_id() -> str:
    """Uniform mission ID for tests that don't check uuid7 properties."""
    return uuid7_str()


@pytest.fixture
def make_envelope():
    """Factory for an EventEnvelope with a MissionDispatched default payload."""

    def _build(
        *,
        mission_id: str | None = None,
        prompt: str = "test mission",
        source_actor: str = "hauptjarvis",
    ) -> EventEnvelope:
        return EventEnvelope(
            mission_id=mission_id or uuid7_str(),
            source_actor=source_actor,  # type: ignore[arg-type]
            ts_ms=now_ms(),
            payload=MissionDispatched(prompt=prompt),
        )

    return _build
