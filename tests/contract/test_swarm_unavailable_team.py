"""Unavailable catalog identities stay honest across storage and browser DTOs."""

import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from jarvis.core.swarm_types import TeamCreate, TeamListItem, TeamUnavailable
from jarvis.swarm.store import TeamRegistry
from tests.fakes.swarm_storage import StorageClock


def test_corrupt_team_retains_catalog_identity_and_other_teams_remain_readable(tmp_path):
    registry = TeamRegistry(tmp_path / "instance", clock=StorageClock())
    broken = registry.create(TeamCreate(name="Broken", goal="Work", request_key="broken"))
    healthy = registry.create(TeamCreate(name="Healthy", goal="Work", request_key="healthy"))
    store = registry.open(broken["id"])
    store.path.write_bytes(b"Deliberately invalid SQLite database")

    rows = TypeAdapter(list[TeamListItem]).validate_python(registry.list())
    unavailable = next(row for row in rows if row.id == broken["id"])
    assert isinstance(unavailable, TeamUnavailable)
    assert unavailable.name == broken["name"]
    assert unavailable.created_at == broken["created_at"]
    assert unavailable.available is False
    assert unavailable.error
    assert "state" not in unavailable.model_dump()
    assert registry.open(healthy["id"]).get()["state"] == "created"


@pytest.mark.parametrize(
    "invented", [{"state": "failed"}, {"tokens_used": "0"}, {"available": True}]
)
def test_unavailable_dto_rejects_invented_execution_fields(invented):
    row = dict(id="known", name="Known team", created_at=1.0, available=False, error="Unavailable")
    with pytest.raises(ValidationError):
        TeamUnavailable.model_validate(row | invented)


def test_unavailable_python_typescript_field_parity():
    path = (
        Path(__file__).resolve().parents[2] / "jarvis/ui/web/frontend/src/components/swarm/types.ts"
    )
    source = path.read_text(encoding="utf-8")
    declaration = re.search(r"export interface TeamUnavailable\s*{([^}]+)}", source)
    assert declaration is not None
    fields = dict(re.findall(r"(\w+):\s*(\w+)\s*;", declaration.group(1)))
    assert fields == {
        "id": "string",
        "name": "string",
        "created_at": "number",
        "available": "false",
        "error": "string",
    }
    assert set(fields) == set(TeamUnavailable.model_fields)
