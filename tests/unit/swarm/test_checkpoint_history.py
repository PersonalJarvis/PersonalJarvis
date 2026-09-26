"""Checkpoint identity, bounded history, replay and shared transactional writers."""

import pytest

from jarvis.swarm.control_requests import drain, enqueue
from jarvis.swarm.store import SwarmConflictError
from jarvis.swarm.supervision import queue_turn, save_progress
from tests.fakes.swarm_storage import running_team


def history(fixture):
    return [
        record
        for record in fixture.store.records("decisions", limit=200)
        if record.get("kind") == "checkpoint_snapshot"
    ]


def test_checkpoint_identity_versions_and_operation_replay(tmp_path):
    fixture = running_team(tmp_path)
    initial = fixture.store.get()["version"]
    first = fixture.store.checkpoint(
        fixture.controller, {"plan": "First"}, initial, request_key="one"
    )
    one = history(fixture)[0]
    assert first["checkpoint"] == {"plan": "First"}
    assert one["version"] == "1"
    assert (
        fixture.store.checkpoint(fixture.controller, {"plan": "First"}, initial, request_key="one")
        == first
    )
    assert fixture.store.checkpoint(fixture.controller, {"plan": "First"}) == first
    with pytest.raises(SwarmConflictError, match="different content"):
        fixture.store.checkpoint(fixture.controller, {"plan": "Other"}, request_key="one")
    fixture.store.checkpoint(fixture.controller, {"plan": "Second"}, request_key="two")
    records = history(fixture)
    assert [record["version"] for record in records] == ["1", "2"]
    assert len({record["checkpoint_id"] for record in records}) == 1
    event = next(
        record for record in fixture.store.records("events") if record["kind"] == "team.checkpoint"
    )
    assert event["data"]["snapshot_id"] == records[-1]["id"]
    assert event["data"]["checkpoint_version"] == "2"


def test_checkpoint_history_retention_does_not_reset_identity_or_version(tmp_path):
    fixture = running_team(tmp_path)
    for number in range(40):
        fixture.store.checkpoint(fixture.controller, {"iteration": number})
    records = history(fixture)
    assert len(records) == 32
    assert [record["version"] for record in records] == [str(number) for number in range(9, 41)]
    assert len({record["checkpoint_id"] for record in records}) == 1
    assert fixture.store.get()["checkpoint"] == {"iteration": 39}
    public = fixture.store.records("checkpoints", limit=200)
    assert len(public) == 32
    assert public[0]["version"] == "40" and public[-1]["version"] == "9"


def test_all_runtime_checkpoint_writers_merge_and_share_one_history(tmp_path):
    fixture = running_team(tmp_path)
    fixture.store.checkpoint(fixture.controller, {"plan": "Keep original objective"})
    save_progress(fixture.store, fixture.controller, {"last_task": "work"})
    fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.clock.now += 6
    queue_turn(fixture.store, fixture.controller)
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    enqueue(
        fixture.store,
        lead,
        "spawn_worker",
        {"reason": "Continue the authorized goal"},
        "new-worker",
    )
    drain(fixture.store, fixture.controller)
    checkpoint = fixture.store.get()["checkpoint"]
    assert checkpoint["plan"] == "Keep original objective"
    assert checkpoint["last_task"] == "work"
    assert checkpoint["supervision"]["turns"] == 1
    assert checkpoint["control_requests"]["last_request_id"]
    records = history(fixture)
    assert len({record["checkpoint_id"] for record in records}) == 1
    assert {record["source"] for record in records} >= {
        "controller",
        "worker.progress",
        "supervision.lead",
        "control.request",
    }


def test_checkpoint_history_rolls_back_with_its_authority_transaction(tmp_path):
    fixture = running_team(tmp_path)
    fixture.store.checkpoint(fixture.controller, {"plan": "Original"})
    before = fixture.store.get()
    with pytest.raises(RuntimeError, match="Synthetic rollback"):
        with fixture.store._tx(write=True) as connection:
            fixture.store._checkpoint(
                connection, fixture.store._team(connection), {"plan": "Invalid"}, source="synthetic"
            )
            raise RuntimeError("Synthetic rollback")
    assert fixture.store.get() == before
    assert len(history(fixture)) == 1
