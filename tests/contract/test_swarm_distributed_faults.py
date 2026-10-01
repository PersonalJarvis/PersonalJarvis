"""Opt-in process faults against the three explicitly disposable loopback services.

Set both SWARM_DISTRIBUTED_TEST=1 and SWARM_DISTRIBUTED_FAULT_TEST=1. This file
must run alone: it stops and starts codex-swarm-redis and codex-swarm-s3. It never
changes another container, existing bucket policy, or application credentials.
SWARM_DISTRIBUTED_FAULT_REPORT_DIR optionally preserves sanitized measurements.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.swarm_types import CapabilityPolicy
from jarvis.swarm.distributed.database import team_schema
from jarvis.swarm.distributed.registry import PostgresTeamRegistry
from jarvis.swarm.store import SwarmAccessError, SwarmBudgetError, SwarmStoreError
from tests.contract.test_swarm_distributed import registries, running  # noqa: F401
from tests.fakes.swarm_storage import accepted_result

pytestmark = pytest.mark.skipif(
    os.environ.get("SWARM_DISTRIBUTED_TEST") != "1"
    or os.environ.get("SWARM_DISTRIBUTED_FAULT_TEST") != "1",
    reason="Process faults require explicit exclusive disposable-service opt-in",
)

_SERVICES = {
    "codex-swarm-pg": ("postgres:17-alpine", "5432/tcp", "55434"),
    "codex-swarm-redis": ("redis:alpine", "6379/tcp", "56381"),
    "codex-swarm-s3": ("chrislusf/seaweedfs:latest", "8333/tcp", "58334"),
}


def _docker(*arguments, input_text=None, timeout=45):
    result = subprocess.run(
        ["docker", *arguments],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    # Never print raw inspect/config output: it may contain service credentials.
    assert result.returncode == 0, f"Docker {arguments[0]} failed ({result.returncode})"
    return result.stdout


def _inspect(name):
    assert name in _SERVICES
    details = json.loads(_docker("inspect", name))[0]
    image, port, host_port = _SERVICES[name]
    assert details["Name"] == "/" + name and details["Config"]["Image"] == image
    assert details["HostConfig"]["PortBindings"] == {
        port: [{"HostIp": "127.0.0.1", "HostPort": host_port}]
    }, "Refuse to mutate a service with an unexpected network configuration"
    return {
        "name": name,
        "container_id": details["Id"][:12],
        "image": image,
        "image_id": details["Image"],
        "running": details["State"]["Running"],
        "started_at": details["State"]["StartedAt"],
        "loopback_port": host_port,
    }


def _healthy(check, *, timeout=40):
    deadline = time.monotonic() + timeout
    while True:
        try:
            check()
            return
        except SwarmStoreError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


@pytest.fixture
def proof(request, tmp_path):
    assert not os.environ.get("PYTEST_XDIST_WORKER"), "Fault campaign requires exclusive execution"
    report_dir = Path(os.environ.get("SWARM_DISTRIBUTED_FAULT_REPORT_DIR", tmp_path))
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "test": request.node.name,
        "started_at": datetime.now(UTC).isoformat(),
        "topology": [_inspect(name) for name in _SERVICES],
        "outages": [],
        "observations": {},
        "clock": "Real service outages; deterministic application lease/outbox clock",
        "status": "incomplete",
    }
    assert all(service["running"] for service in report["topology"])
    try:
        yield report
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["final_topology"] = [_inspect(name) for name in _SERVICES]
        path = report_dir / (request.node.name + ".json")
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


@contextmanager
def _outage(name, check, report):
    before = _inspect(name)
    assert before["running"]
    measurement = {"service": name, "before": before}
    report["outages"].append(measurement)
    begun = time.monotonic()
    try:
        _docker("stop", "--time", "2", name)
        assert not _inspect(name)["running"]
        stopped = time.monotonic()
        measurement["stop_command_seconds"] = stopped - begun
        with pytest.raises(SwarmStoreError):
            check()
        measurement["real_connection_failure_confirmed"] = True
        yield measurement
    finally:
        restart_begun = time.monotonic()
        _docker("start", name)
        _healthy(check)
        measurement["recovery_seconds"] = time.monotonic() - restart_begun
        measurement["stop_through_healthy_seconds"] = time.monotonic() - begun
        measurement["after"] = _inspect(name)
        assert measurement["after"]["running"]
        assert measurement["after"]["started_at"] != before["started_at"]


def _outbox(store):
    with store._tx() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT event_id,event_seq,published_at,acknowledged_at,attempts "
                "FROM delivery_outbox ORDER BY event_seq"
            ).fetchall()
        ]


def test_literal_redis_restart_preserves_pg_results_and_acknowledgements(registries, proof):  # noqa: F811
    make, clock = registries
    registry = make(real_objects=True)
    first, other = running(registry), running(registry)
    store, delivery = first.store, registry.delivery
    artifact = store.capture_result(first.controller, first.actor, "Proof", "checked", "result")
    assert store.pump_delivery() > 0
    pending = delivery.poll(store, "consumer-before-restart")
    assert pending
    original_ids = {row["event_id"] for row in _outbox(store)}
    with _outage("codex-swarm-redis", delivery.check_connection, proof):
        result = accepted_result(first, first.actor, result="checked", evidence=[artifact["id"]])
        assert result["state"] == "succeeded"
        with pytest.raises(SwarmStoreError, match="durable acknowledgement was retained"):
            delivery.ack(store, pending[0])
        with pytest.raises(SwarmStoreError, match="remain queued in PostgreSQL"):
            store.pump_delivery()
        rows = _outbox(store)
        assert original_ids <= {row["event_id"] for row in rows}
        assert (
            next(row for row in rows if row["event_id"] == pending[0].event_id)["acknowledged_at"]
            is not None
        )
        expected = {row["event_id"] for row in rows if row["acknowledged_at"] is None}
        assert other.store.heartbeat(other.actor)
        with pytest.raises(SwarmAccessError):
            other.store.read_task(first.actor, "work")
    clock.now += 31
    assert store.pump_delivery() == len(expected)
    recovered = delivery.poll(store, "consumer-after-restart")
    assert {hint.event_id for hint in recovered} == expected
    assert all(hint.team_id == store.team_id for hint in recovered)
    delivery.ack_many(store, recovered)
    # Replaying the accepted effect cannot create another result or rating.
    assert (
        store.finish(
            first.actor, result["result"], result["evidence"], {}, controller=first.controller
        )
        == result
    )
    assert len(store.records("reputation")) == 1
    with store._tx() as connection:
        assert connection.execute("SELECT count(*) FROM verifications").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    foreign = other.store.events_after()[0]
    # The broker is non-authoritative: old and cross-team references are filtered.
    delivery.client().xadd(
        delivery.stream(store.team_id), {"event_id": foreign["id"], "event_seq": foreign["seq"]}
    )
    delivery.client().xadd(
        delivery.stream(store.team_id),
        {"event_id": recovered[0].event_id, "event_seq": recovered[0].event_seq},
    )
    assert delivery.poll(store, "consumer-after-restart") == []
    with pytest.raises(SwarmAccessError):
        delivery.ack(other.store, recovered[0])
    clock.now += 31
    assert store.pump_delivery() == 0
    assert len({event["id"] for event in store.events_after()}) == len(store.events_after())
    retained = store.retain(first.controller, clock.now)
    assert int(retained["acknowledged_delivery_hints"]) == len(rows)
    assert _outbox(store) == []
    assert store.records("tasks")[0]["state"] == "succeeded"
    proof["observations"].update(
        teams=2,
        tasks=2,
        committed_result_count=1,
        verification_count=1,
        unacknowledged_events_recovered=len(expected),
        durable_acknowledgement_retained_during_outage=True,
        cross_team_and_duplicate_broker_references_rejected=True,
        acknowledged_outbox_rows_pruned=len(rows),
    )
    proof["status"] = "test_assertions_completed"


def test_literal_s3_outage_keeps_unknown_quota_and_recovers_one_artifact(registries, proof):  # noqa: F811
    make, _ = registries
    registry = make(real_objects=True)
    first = running(registry, policy=CapabilityPolicy(max_artifact_bytes="6"))
    other = running(registry)
    store = first.store
    previous = store.capture_result(first.controller, first.actor, "Previous", "old", "previous")
    other_artifact = other.store.capture_result(
        other.controller, other.actor, "Other", "old", "other"
    )
    assert registry.objects.key(store.team_id, previous["sha256"]) != registry.objects.key(
        other.store.team_id, other_artifact["sha256"]
    )
    with _outage("codex-swarm-s3", registry.objects.check_connection, proof):
        with pytest.raises(SwarmStoreError, match="S3 object unavailable"):
            store.download_artifact(previous["id"])
        with pytest.raises(SwarmStoreError, match="S3 object write failed"):
            store.capture_result(first.controller, first.actor, "Recovered", "new", "retry")
        with store._tx() as connection:
            row = connection.execute(
                "SELECT state,size_bytes FROM object_uploads WHERE state<>'completed'"
            ).fetchone()
            assert row["state"] == "unknown" and row["size_bytes"] == 3
            assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
        with pytest.raises(SwarmBudgetError, match="pending or unknown"):
            store.capture_result(first.controller, first.actor, "Excess", "x", "excess")
        begun = time.monotonic()
        assert store.heartbeat(first.actor)
        store.checkpoint(first.controller, {"phase": "S3 unavailable; PG remains usable"})
        proof["observations"]["pg_heartbeat_and_checkpoint_seconds"] = time.monotonic() - begun
        with pytest.raises(SwarmAccessError):
            other.store.download_artifact(previous["id"])
    recovered = store.capture_result(first.controller, first.actor, "Recovered", "new", "retry")
    assert (
        store.capture_result(first.controller, first.actor, "Recovered", "new", "retry")["id"]
        == recovered["id"]
    )
    assert store.download_artifact(previous["id"])[1] == b"old"
    assert store.download_artifact(recovered["id"])[1] == b"new"
    assert other.store.download_artifact(other_artifact["id"])[1] == b"old"
    with store._tx() as connection:
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT count(*) FROM object_uploads WHERE state<>'completed'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT value FROM counters WHERE name='artifact_bytes'").fetchone()[
                0
            ]
            == "6"
        )
    result = accepted_result(first, first.actor, result="new", evidence=[recovered["id"]])
    assert (
        store.finish(
            first.actor, result["result"], result["evidence"], {}, controller=first.controller
        )
        == result
    )
    assert len(store.records("reputation")) == 1
    proof["observations"].update(
        teams=2,
        tasks=2,
        unknown_upload_reserved_bytes=3,
        committed_artifacts_after_recovery=2,
        artifact_bytes_after_recovery=6,
        committed_result_count=1,
        original_and_other_team_bytes_preserved=True,
        cross_team_read_rejected_during_outage=True,
    )
    proof["status"] = "test_assertions_completed"


def test_real_s3_lifecycle_expires_only_deleted_team_prefix(registries, proof):  # noqa: F811
    make, clock = registries
    template = make(real_objects=True)
    client = template.objects.client()
    bucket = "swarm-fault-" + uuid.uuid4().hex[:16]
    client.create_bucket(Bucket=bucket)
    registry = PostgresTeamRegistry(
        template.config.model_copy(
            update={"s3_bucket": bucket, "namespace": "fault_" + uuid.uuid4().hex[:12]}
        ),
        template.objects.secrets,
        clock=clock,
    )
    teams = []
    try:
        registry.provision()
        first, other = running(registry), running(registry)
        teams = [first.team["id"], other.team["id"]]
        artifact = first.store.capture_result(
            first.controller, first.actor, "Expired", "old", "old"
        )
        live = other.store.capture_result(other.controller, other.actor, "Live", "keep", "keep")
        first.store.retain(first.controller, clock.now + 31)
        assert first.store.download_artifact(artifact["id"])[1] == b"old"
        first.store.user_transition("canceled")
        receipt = registry.delete(first.team["id"], request_key="delete-before-expiration")
        assert receipt["objects"] == "retained_for_bucket_lifecycle"
        assert registry.delete(first.team["id"], request_key="delete-before-expiration")["deleted"]
        with pytest.raises(SwarmAccessError):
            registry.open(first.team["id"])
        # Deletion never removes bytes before its PostgreSQL transaction commits.
        assert registry.objects.read(first.team["id"], artifact) == b"old"
        prefix = registry.config.namespace + "/" + first.team["id"] + "/"
        expiration = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)
        # This is an explicit provider/operator policy for an already deleted team,
        # not an assertion that Jarvis configures automatic bucket lifecycle rules.
        client.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "expire-deleted-test-team-only",
                        "Status": "Enabled",
                        "Filter": {"Prefix": prefix},
                        "Expiration": {"Date": expiration},
                    }
                ]
            },
        )
        rules = client.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
        assert rules[0]["Filter"]["Prefix"] == prefix
        # Refuse a shared sweep when any unrelated bucket has an active policy.
        for entry in client.list_buckets()["Buckets"]:
            if entry["Name"] == bucket:
                continue
            try:
                policy = client.get_bucket_lifecycle_configuration(Bucket=entry["Name"])
            except client.exceptions.ClientError as error:
                assert error.response["Error"]["Code"] == "NoSuchLifecycleConfiguration"
            else:
                assert not any(rule["Status"] == "Enabled" for rule in policy.get("Rules", []))
        begun = time.monotonic()
        output = _docker(
            "exec",
            "-i",
            "codex-swarm-s3",
            "weed",
            "shell",
            input_text=(
                "s3.lifecycle.run-shard -shards 0-15 -s3 localhost:18333 -runtime 10s -events 500\n"
            ),
        )
        assert "loaded lifecycle for 1 bucket(s)" in output
        with pytest.raises(SwarmStoreError, match="S3 object unavailable"):
            registry.objects.read(first.team["id"], artifact)
        remaining = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        assert [entry["Key"] for entry in remaining] == [
            registry.objects.key(other.team["id"], live["sha256"])
        ]
        assert other.store.download_artifact(live["id"])[1] == b"keep"
        assert other.store.read_task(other.actor, "work")["state"] == "running"
        with registry.database.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM team_deletions").fetchone()[0] == 1
        proof["observations"].update(
            teams=2,
            objects_before=2,
            expired_deleted_team_objects=1,
            live_team_objects_preserved=1,
            exact_remaining_object_listing_verified=True,
            lifecycle_worker_seconds=time.monotonic() - begun,
            lifecycle_policy="Exact deleted-team prefix; absolute expiration date already due",
            lifecycle_trigger="Real SeaweedFS lifecycle worker invoked by test operator",
            scheduled_background_expiration_proven=False,
            deletion_precedes_object_expiration=True,
            lifecycle_worker_output=output.strip(),
        )
    finally:
        # Remove only this test's exact unique bucket and database namespaces.
        client.delete_bucket_lifecycle(Bucket=bucket)
        for entry in client.list_objects_v2(Bucket=bucket).get("Contents", []):
            client.delete_object(Bucket=bucket, Key=entry["Key"])
        client.delete_bucket(Bucket=bucket)
        with registry.database.transaction(write=True) as connection:
            for team_id in teams:
                schema = team_schema(registry.config.namespace, team_id)
                connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                registry.delivery.delete_team(team_id)
            connection.raw.execute(f'DROP SCHEMA "{registry.config.namespace}" CASCADE')
        registry.close()
    proof["status"] = "test_assertions_completed"
