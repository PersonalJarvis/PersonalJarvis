"""Checkpoint storage/Pydantic/wire identity remains exact and owner-scoped."""

import json

import pytest
from pydantic import ValidationError

from jarvis.core.protocols import CheckpointSnapshot
from jarvis.swarm.store import SwarmAccessError
from tests.fakes.swarm_storage import running_team


def test_checkpoint_snapshot_model_is_used_by_storage_and_owner_inspection(tmp_path):
    fixture = running_team(tmp_path)
    fixture.store.checkpoint(fixture.controller, {"goal_cursor": "9007199254740993"})
    snapshot = fixture.store.records("checkpoints")[0]
    model = CheckpointSnapshot.model_validate(snapshot)
    assert json.loads(model.model_dump_json())["version"] == "1"
    assert fixture.store.get_record("checkpoints", snapshot["id"]) == snapshot
    assert (
        fixture.store.get_record("decisions", snapshot["checkpoint_id"])["kind"]
        == "checkpoint_head"
    )
    with pytest.raises(SwarmAccessError, match="snapshot"):
        fixture.store.get_record("checkpoints", snapshot["checkpoint_id"])
    other = running_team(tmp_path / "other")
    with pytest.raises(SwarmAccessError):
        other.store.get_record("checkpoints", snapshot["id"])
    precise = CheckpointSnapshot.model_validate(dict(snapshot, version="9007199254740993"))
    assert json.loads(precise.model_dump_json())["version"] == "9007199254740993"
    with pytest.raises(ValidationError):
        CheckpointSnapshot.model_validate(dict(snapshot, version=0))
