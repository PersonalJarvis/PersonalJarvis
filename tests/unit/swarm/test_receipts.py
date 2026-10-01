"""Runtime provenance cannot be minted by artifact names, content or old uploads."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jarvis.swarm.distributed.artifacts import RemoteArtifacts
from jarvis.swarm.receipts import build_receipt, canonical, is_runtime_receipt, value_hash
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, TeamRegistry, TeamStore
from tests.fakes.swarm_storage import running_team


def execution(**changes):
    return {
        "script": "function main(input) { return input.value * 2; }",
        "inputs": {"value": 2},
        "execution": {"output": 4, "stdout": "ok", "stderr": "", "exit_code": 0, "duration_ms": 1},
        **changes,
    }


def active(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    return fixture, actor


def capture(fixture, actor, request_key="receipt", **changes):
    return fixture.store.capture_receipt(
        fixture.controller, actor, "execution", execution(**changes), [], request_key, "trace-1"
    )


def test_execution_receipt_binds_observation_hashes_without_credentials(tmp_path):
    fixture, actor = active(tmp_path)
    record = capture(fixture, actor, script_sha256="forged")
    metadata, content = fixture.store.download_artifact(record["id"])
    receipt = json.loads(content)
    assert metadata == record
    assert is_runtime_receipt(record, "execution", actor)
    assert receipt["source_attempt"] == actor.task_fence
    assert receipt["controller_fence"] == fixture.controller.fence
    assert receipt["trace_id"] == "trace-1"
    assert (
        receipt["payload"]["script_sha256"]
        == hashlib.sha256(execution()["script"].encode()).hexdigest()
    )
    assert receipt["payload"]["input_sha256"] == value_hash({"value": 2})
    assert receipt["payload"]["output_sha256"] == value_hash(4)
    assert receipt["payload"]["exit_sha256"] == value_hash(0)
    assert actor.token not in content.decode()
    assert fixture.controller.token not in json.dumps(record)
    assert capture(fixture, actor, script_sha256="forged") == record
    with pytest.raises(SwarmConflictError):
        capture(fixture, actor, inputs={"value": 3})


def test_worker_lookalike_and_host_output_never_gain_receipt_authority(tmp_path):
    fixture, actor = active(tmp_path)
    trusted = capture(fixture, actor)
    content = fixture.store.download_artifact(trusted["id"])[1]
    worker = fixture.store.write_artifact(actor, "execution.json", content, "lookalike")
    output = fixture.store.capture_result(
        fixture.controller, actor, "execution.json", content, "output"
    )
    assert worker["provenance"]["origin"] == "worker-authored"
    assert output["provenance"]["origin"] == "worker-output"
    assert not is_runtime_receipt(worker)
    assert not is_runtime_receipt(output)
    with pytest.raises(SwarmConflictError):
        capture(fixture, actor, request_key="lookalike")
    with pytest.raises(TypeError):
        fixture.store.write_artifact(
            actor, "execution.json", content, "x", receipt=json.loads(content)
        )


@pytest.mark.parametrize(
    "authority",
    ["foreign-controller", "forged-controller", "stale-controller", "foreign-actor", "stale-actor"],
)
def test_receipt_minting_revalidates_controller_actor_and_attempt(tmp_path, authority):
    fixture, actor = active(tmp_path / "own")
    other, foreign = active(tmp_path / "foreign")
    controller = fixture.controller
    if authority == "foreign-controller":
        controller = other.controller
    elif authority == "forged-controller":
        controller = replace(controller, token="not-a-controller")  # noqa: S106
    elif authority == "stale-controller":
        fixture.store.release_controller(controller)
    elif authority == "foreign-actor":
        actor = foreign
    else:
        fixture.store.fail(actor, "retry", controller=controller)
        fixture.store.claim(controller, actor.agent_id, actor.task_id)
    with pytest.raises(SwarmAccessError):
        fixture.store.capture_receipt(controller, actor, "execution", execution(), [], "denied")
    assert fixture.store.records("artifacts") == []


def test_http_receipt_keeps_url_status_and_requires_matching_scoped_body(tmp_path):
    fixture, actor = active(tmp_path / "own")
    other, foreign = active(tmp_path / "foreign")
    body = fixture.store.capture_result(fixture.controller, actor, "source.txt", "observed", "body")
    payload = {
        "url": "https://example.test/reference",
        "status": 200,
        "body_sha256": body["sha256"],
    }
    receipt = fixture.store.capture_receipt(
        fixture.controller, actor, "http", payload, [body["id"]], "http"
    )
    assert is_runtime_receipt(receipt, "http", actor)
    assert json.loads(fixture.store.download_artifact(receipt["id"])[1])["payload"] == payload
    unrelated = other.store.write_artifact(foreign, "same", "observed", "same")
    for subjects in ([], [unrelated["id"]]):
        with pytest.raises(SwarmAccessError):
            fixture.store.capture_receipt(
                fixture.controller, actor, "http", payload, subjects, "denied"
            )
    with pytest.raises(SwarmAccessError):
        fixture.store.capture_receipt(
            fixture.controller,
            actor,
            "http",
            dict(payload, body_sha256="0" * 64),
            [body["id"]],
            "wrong-hash",
        )


def test_stale_subject_and_secret_payload_are_rejected(tmp_path):
    fixture, actor = active(tmp_path)
    old = capture(fixture, actor)
    fixture.store.fail(actor, "retry", controller=fixture.controller)
    retry = fixture.store.claim(fixture.controller, actor.agent_id, actor.task_id)
    assert not is_runtime_receipt(old, "execution", retry)
    with pytest.raises(SwarmAccessError):
        fixture.store.capture_receipt(
            fixture.controller, retry, "execution", execution(), [old["id"]], "stale-subject"
        )
    for credential in (retry.token, fixture.controller.token):
        with pytest.raises(ValueError, match="credentials"):
            capture(fixture, retry, inputs={"leak": credential})


def test_dependency_verification_and_backup_preserve_receipt_bindings(tmp_path):
    fixture, actor = active(tmp_path / "original")
    dependency = {
        "package": "example",
        "version": "1.2.3",
        "sha256": "a" * 64,
        "registry": "registry.example.test",
        "entrypoint": "index.js",
        "files": {"index.js": "export default 1"},
    }
    dep = fixture.store.capture_receipt(
        fixture.controller, actor, "dependency", dependency, [], "dep"
    )
    verification = {
        "kind": "review",
        "accepted": True,
        "contract_hash": "b" * 64,
        "verifier_id": "independent-verifier",
        "reason": "Checked",
    }
    proof = fixture.store.capture_receipt(
        fixture.controller, actor, "verification", verification, [dep["id"]], "verify"
    )
    fixture.store.backup(tmp_path / "backup")
    registry = TeamRegistry(tmp_path / "restored", clock=fixture.clock)
    team = registry.restore(tmp_path / "backup", request_key="restore")
    restored = registry.open(team["id"])
    for record in (dep, proof):
        metadata, content = restored.download_artifact(record["id"])
        assert metadata == record
        assert is_runtime_receipt(metadata)
        assert json.loads(content)["source_attempt"] == actor.task_fence
    with pytest.raises(SwarmAccessError):
        restored.capture_receipt(fixture.controller, actor, "execution", execution(), [], "stale")


class MemoryObjects:
    def __init__(self):
        self.data = {}
        self.fail = False

    def put(self, team_id, digest, data):
        self.data[(team_id, digest)] = bytes(data)
        if self.fail:
            raise OSError("Upload acknowledgement was lost")
        return {"storage": "s3", "version_id": "test-version"}

    def read(self, team_id, record):
        return self.data[(team_id, record["sha256"])]


class RemoteStore(RemoteArtifacts, TeamStore):
    def _read_object(self, record):
        return self.registry.objects.read(self.team_id, record)


def remote_fixture(tmp_path):
    fixture, actor = active(tmp_path)
    fixture.store = RemoteStore(fixture.store.path, fixture.team["id"], clock=fixture.clock)
    fixture.store.registry = SimpleNamespace(objects=MemoryObjects())
    with fixture.store._tx(write=True) as connection:
        connection.execute(
            "CREATE TABLE object_uploads (id TEXT PRIMARY KEY, owner_id TEXT, task_id TEXT, "
            "fingerprint TEXT, size_bytes INTEGER, state TEXT, created_at REAL, "
            "task_fence INTEGER, sha256 TEXT, name TEXT, media_type TEXT, request_key TEXT, "
            "recovered_artifact_id TEXT NOT NULL DEFAULT '')"
        )
    return fixture, actor


def test_remote_receipt_commit_and_retry_preserve_authority_and_exact_quota(tmp_path):
    fixture, actor = remote_fixture(tmp_path)
    record = capture(fixture, actor)
    assert capture(fixture, actor) == record
    assert is_runtime_receipt(record, "execution", actor)
    assert (
        json.loads(fixture.store.download_artifact(record["id"])[1])["origin"] == "runtime-receipt"
    )
    with fixture.store._tx() as connection:
        assert (
            connection.execute("SELECT value FROM counters WHERE name='artifact_bytes'").fetchone()[
                0
            ]
            == record["size_bytes"]
        )


@pytest.mark.parametrize("trusted", [True, False])
def test_unknown_upload_recovery_preserves_original_trust_without_promoting_replacement(
    tmp_path, trusted
):
    fixture, actor = remote_fixture(tmp_path)
    envelope = build_receipt(fixture.controller, actor, "execution", execution(), [], "trace-1")
    fixture.store.registry.objects.fail = True
    with pytest.raises(OSError):
        if trusted:
            capture(fixture, actor)
        else:
            fixture.store.write_artifact(
                actor,
                "execution.json",
                canonical(envelope),
                "receipt",
                media_type="application/json",
            )
    fixture.store.registry.objects.fail = False
    if not trusted:
        with pytest.raises(SwarmConflictError):
            capture(fixture, actor)
    fixture.store.fail(actor, "replacement", controller=fixture.controller)
    retry = fixture.store.claim(fixture.controller, actor.agent_id, actor.task_id)
    pending = fixture.store.pending_uploads(fixture.controller, retry)
    assert len(pending) == 1
    recovered = fixture.store.recover_upload(fixture.controller, retry, pending[0]["id"])
    assert recovered["provenance"]["origin"] == "worker-output"
    assert not is_runtime_receipt(recovered, actor=retry)
    source = recovered["recovered_from"]
    assert source["source_attempt"] == actor.task_fence
    assert ("provenance" in source) is trusted
    if trusted:
        assert source["provenance"]["origin"] == "runtime-receipt"
        assert source["provenance"]["source_attempt"] == actor.task_fence
    assert fixture.store.recover_upload(fixture.controller, retry, pending[0]["id"]) == recovered
