"""Artifact provenance, generated-tool registration and publication durability."""

import json

import pytest

from jarvis.core.swarm_types import CapabilityPolicy
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, SwarmStoreError, TeamRegistry
from tests.fakes.swarm_storage import accepted_result, running_team


def test_immutable_artifact_dedup_and_integrity(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    record = fixture.store.write_artifact(actor, "Output", "data", "write")
    assert fixture.store.write_artifact(actor, "Output", "data", "write") == record
    with pytest.raises(SwarmConflictError):
        fixture.store.write_artifact(actor, "Output", "changed", "write")
    assert fixture.store.read_artifact(actor, record["id"])["content"] == "data"
    path = fixture.store.path.parent / "objects" / record["object_key"]
    path.write_text("corrupt", encoding="utf-8")
    with pytest.raises(SwarmStoreError):
        fixture.store.read_artifact(actor, record["id"])


def test_runtime_capture_survives_disabled_worker_write_policy(tmp_path):
    fixture = running_team(tmp_path, policy=CapabilityPolicy(tools=[]))
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    with pytest.raises(SwarmAccessError):
        fixture.store.write_artifact(actor, "unauthorized", "data", "worker")
    artifact = fixture.store.capture_result(fixture.controller, actor, "Result", "data", "host")
    assert fixture.store.download_artifact(artifact["id"])[1] == b"data"
    with pytest.raises(SwarmAccessError):
        fixture.store.read_artifact(actor, artifact["id"])


def test_generated_tool_is_team_local_immutable_and_evidence_bound(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    artifact = fixture.store.capture_result(
        fixture.controller, actor, "Tests", "All passed", "test-result"
    )
    args = (
        fixture.controller,
        actor,
        "double",
        {"type": "object"},
        "function main(x){return x*2}",
        [{"input": 2, "expected": 4}],
        artifact["id"],
        "register",
    )
    tool = fixture.store.register_tool(*args)
    assert fixture.store.register_tool(*args) == tool
    assert fixture.store.get_tool(actor, "double")["scope"] == "team"
    assert "script" not in fixture.store.tool_catalog(actor)[0]
    changed = list(args)
    changed[4] = "function main(x){return x*3}"
    with pytest.raises(SwarmConflictError):
        fixture.store.register_tool(*changed)
    fixture.store.fail(actor, "retry", controller=fixture.controller)
    retry = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    changed[1] = retry
    changed[2] = "stale_evidence"
    with pytest.raises(SwarmAccessError):
        fixture.store.register_tool(*changed)


def test_worker_cannot_publish_and_lead_needs_accepted_output_and_grant(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    artifact = fixture.store.write_artifact(actor, "Output", "data", "output")
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    fixture.store.authorize_destination("requested-conversation", kind="conversation")
    with pytest.raises(SwarmAccessError):
        fixture.store.queue_publication(
            fixture.controller, actor, artifact["id"], "requested-conversation", "publish"
        )
    with pytest.raises(SwarmAccessError):
        fixture.store.queue_publication(
            fixture.controller, lead, artifact["id"], "requested-conversation", "publish"
        )
    accepted_result(fixture, actor, evidence=[artifact["id"]])
    with pytest.raises(SwarmAccessError):
        fixture.store.queue_publication(
            fixture.controller, lead, artifact["id"], "unguarded-destination", "publish"
        )
    publication = fixture.store.queue_publication(
        fixture.controller, lead, artifact["id"], "requested-conversation", "publish"
    )
    assert publication["state"] == "pending"
    assert (
        fixture.store.queue_publication(
            fixture.controller, lead, artifact["id"], "requested-conversation", "publish"
        )
        == publication
    )
    assert (
        fixture.registry.open(fixture.team["id"]).pending_publications(fixture.controller)[0]["id"]
        == publication["id"]
    )
    receipt = {"destination_key": "requested-conversation/output", "sha256": artifact["sha256"]}
    done = fixture.store.complete_publication(fixture.controller, publication["id"], receipt)
    assert done["state"] == "published"
    assert (
        fixture.store.complete_publication(fixture.controller, publication["id"], receipt) == done
    )
    with pytest.raises(SwarmConflictError):
        fixture.store.complete_publication(
            fixture.controller, publication["id"], dict(receipt, destination_key="different")
        )
    fixture.store.transition(fixture.controller, "succeeded")
    fixture.store.user_transition("archived")
    assert fixture.store.download_artifact(artifact["id"])[1] == b"data"


def test_grant_revocation_prevents_pending_publication_retry_and_completion(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    result = accepted_result(fixture, actor)
    artifact = fixture.store.download_artifact(result["evidence"][0])[0]
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    fixture.store.authorize_destination("approved")
    publication = fixture.store.queue_publication(
        fixture.controller, lead, artifact["id"], "approved", "publish"
    )
    fixture.store.revoke_destination("approved")
    assert fixture.store.pending_publications(fixture.controller) == []
    with pytest.raises(SwarmAccessError):
        fixture.store.queue_publication(
            fixture.controller, lead, artifact["id"], "approved", "publish"
        )
    with pytest.raises(SwarmAccessError):
        fixture.store.complete_publication(
            fixture.controller,
            publication["id"],
            {"destination_key": "approved", "sha256": artifact["sha256"]},
        )


def test_backup_restore_preserves_objects_approval_links_and_fences_execution(tmp_path):
    fixture = running_team(tmp_path / "original")
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    artifact = fixture.store.write_artifact(actor, "Output", "data", "output")
    fixture.store.authorize_destination("reviewed-wiki", kind="reviewed_knowledge")
    snapshot = fixture.store.backup(tmp_path / "backup")
    registry = TeamRegistry(tmp_path / "restored", clock=fixture.clock)
    restored_team = registry.restore(snapshot["path"], owner="new-owner", request_key="restore")
    assert restored_team["id"] == fixture.team["id"]
    restored = registry.open(restored_team["id"], owner="new-owner")
    assert restored.download_artifact(artifact["id"])[1] == b"data"
    assert (
        restored.authorize_destination("reviewed-wiki", kind="reviewed_knowledge")["version"] == 1
    )
    assert restored.ready_tasks()[0]["attempt_count"] == 0
    with pytest.raises(SwarmAccessError):
        restored.validate_actor(actor)
    with pytest.raises(SwarmAccessError):
        restored.renew_controller(fixture.controller)
    assert (
        registry.restore(snapshot["path"], owner="new-owner", request_key="restore")["id"]
        == restored_team["id"]
    )
    assert fixture.store.read_artifact(actor, artifact["id"])["content"] == "data"


def test_restore_rejects_corrupt_objects_without_catalog_registration(tmp_path):
    fixture = running_team(tmp_path / "original")
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    artifact = fixture.store.write_artifact(actor, "Output", "data", "output")
    fixture.store.backup(tmp_path / "backup")
    (tmp_path / "backup" / "objects" / artifact["object_key"]).write_bytes(b"corrupt")
    registry = TeamRegistry(tmp_path / "restored")
    with pytest.raises(SwarmStoreError):
        registry.restore(tmp_path / "backup", request_key="restore")
    assert registry.list() == []
    assert not (registry.root / fixture.team["id"]).exists()


def test_restore_rejects_manifest_identity_and_preserves_existing_team(tmp_path):
    fixture = running_team(tmp_path / "original")
    fixture.store.backup(tmp_path / "backup")
    with pytest.raises(SwarmConflictError):
        fixture.registry.restore(tmp_path / "backup", request_key="restore")
    manifest = tmp_path / "backup" / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["team_id"] = "../escape"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SwarmStoreError):
        TeamRegistry(tmp_path / "restore").restore(tmp_path / "backup", request_key="restore")
    assert fixture.store.get()["state"] == "running"


def test_delete_requires_owner_and_terminal_state_preserving_other_teams(tmp_path):
    fixture = running_team(tmp_path / "instance")
    other = running_team(tmp_path / "other")
    with pytest.raises(SwarmAccessError):
        fixture.registry.delete(fixture.team["id"], "other-owner")
    with pytest.raises(SwarmConflictError):
        fixture.registry.delete(fixture.team["id"])
    fixture.store.user_transition("canceled")
    fixture.store.user_transition("archived")
    publications = fixture.registry.root / "publications"
    publications.mkdir()
    durable = publications / "accepted-output"
    durable.write_bytes(b"accepted")
    assert fixture.registry.delete(fixture.team["id"])["deleted"]
    assert fixture.registry.list() == []
    assert durable.read_bytes() == b"accepted"
    assert other.store.get()["state"] == "running"


def test_indexed_inspector_rejects_foreign_id_and_invalid_collection(tmp_path):
    fixture = running_team(tmp_path)
    assert fixture.store.get_record("tasks", "work")["title"] == "work"
    assert fixture.store.get_record("agents", fixture.team["lead_id"])["role"] == "lead"
    with pytest.raises(SwarmAccessError):
        fixture.store.get_record("agents", "other")
    with pytest.raises(ValueError):
        fixture.store.get_record("agents; DROP TABLE tasks", "other")
