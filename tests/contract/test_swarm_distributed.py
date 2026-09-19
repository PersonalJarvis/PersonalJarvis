"""Real PostgreSQL/Redis family contracts; services must be explicitly selected.

Set SWARM_DISTRIBUTED_TEST=1 for the disposable loopback services documented in
the reference benchmark. Every test gets new PostgreSQL and object namespaces.
The credentials below are exclusively for those disposable test containers.
"""

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from jarvis.core.swarm_types import BudgetLimits, CapabilityPolicy, PeerMessage, TeamCreate
from jarvis.swarm.distributed import DistributedConfig, DistributedSecrets
from jarvis.swarm.distributed.database import team_schema
from jarvis.swarm.distributed.objects import S3Objects
from jarvis.swarm.distributed.registry import PostgresTeamRegistry
from jarvis.swarm.store import (
    SwarmAccessError,
    SwarmBudgetError,
    SwarmConflictError,
    SwarmStoreError,
)
from tests.fakes.swarm_storage import StorageClock, accepted_result, task
from tests.unit.swarm.test_distributed import FakeS3

pytestmark = pytest.mark.skipif(
    os.environ.get("SWARM_DISTRIBUTED_TEST") != "1",
    reason="Explicit disposable PostgreSQL/Redis services are unavailable",
)


@pytest.fixture
def registries():
    pytest.importorskip("psycopg_pool")
    pytest.importorskip("redis")
    instances = []
    clock = StorageClock()

    def create(real_objects=False):
        config = DistributedConfig(
            postgres_dsn="postgresql://swarm_app@127.0.0.1:55434/swarm?sslmode=disable",
            redis_url="redis://127.0.0.1:56381/0",
            s3_endpoint_url="http://127.0.0.1:58334",
            s3_bucket="swarm-tests",
            namespace="test_" + uuid.uuid4().hex[:12],
            allow_insecure_localhost=True,
        )
        credentials = DistributedSecrets(
            "swarm-disposable-app", "swarm-disposable-redis", "test-key", "test-secret"
        )
        objects = S3Objects(config, credentials, client=None if real_objects else FakeS3())
        registry = PostgresTeamRegistry(config, credentials, clock=clock, objects=objects)
        registry.provision()
        instances.append(registry)
        return registry

    yield create, clock
    for registry in instances:
        with registry.database.transaction(write=True) as connection:
            rows = connection.execute("SELECT id FROM teams").fetchall()
            for row in rows:
                schema = team_schema(registry.config.namespace, row["id"])
                connection.raw.execute(f'DROP SCHEMA "{schema}" CASCADE')
                registry.delivery.client().delete(registry.delivery.stream(row["id"]))
            connection.raw.execute(f'DROP SCHEMA "{registry.config.namespace}" CASCADE')
        registry.close()


def running(registry, **changes):
    team = registry.create(
        TeamCreate(
            name="Distributed team",
            goal="Verify distributed authority",
            request_key=uuid.uuid4().hex,
            mode="distributed",
            tasks=[task()],
            **changes,
        )
    )
    store = registry.open(team["id"])
    controller = store.acquire_controller("controller-a")
    store.transition(controller, "running")
    worker = store.add_agent(controller, "Worker")
    actor = store.claim(controller, worker.agent_id, "work")
    return SimpleNamespace(
        registry=registry,
        team=team,
        store=store,
        controller=controller,
        member=worker,
        actor=actor,
        clock=registry.clock,
    )


def test_real_pg_replace_quarantines_corrupt_schema_and_keeps_object_streams(registries, tmp_path):
    make, _ = registries
    registry = make(real_objects=True)
    fixture = running(registry)
    artifact = fixture.store.write_artifact(
        fixture.actor, "proof.txt", "streamed restore proof", "artifact"
    )
    snapshot = tmp_path / "snapshot"
    fixture.store.backup(snapshot)
    schema = team_schema(registry.config.namespace, fixture.team["id"])
    with fixture.store._tx(write=True) as connection:
        connection.raw.execute("DROP TABLE team")
    # A broken remote namespace must not invalidate the mixed owner catalog.
    from pydantic import TypeAdapter

    from jarvis.core.swarm_types import TeamListItem, TeamUnavailable
    from jarvis.swarm.store import TeamRegistry

    local = TeamRegistry(tmp_path / "local")
    healthy = local.create(
        TeamCreate(name="Healthy local", goal="Keep working", request_key="healthy")
    )
    catalog = TypeAdapter(list[TeamListItem]).validate_python(local.list() + registry.list())
    assert next(row for row in catalog if row.id == healthy["id"]).state == "created"
    assert isinstance(next(row for row in catalog if row.id == fixture.team["id"]), TeamUnavailable)
    result = registry.restore(snapshot, request_key="replace-corrupt", replace_existing=True)
    assert result["id"] == fixture.team["id"] and result["state"] == "paused"
    assert result["lead_id"] == fixture.team["lead_id"]
    assert (
        registry.open(result["id"]).download_artifact(artifact["id"])[1]
        == b"streamed restore proof"
    )
    with pytest.raises(SwarmAccessError):
        registry.open(result["id"]).read_task(fixture.actor, "work")
    assert (
        registry.restore(snapshot, request_key="replace-corrupt", replace_existing=True)["id"]
        == result["id"]
    )
    with registry.database.transaction(write=True) as connection:
        quarantine = connection.execute(
            "SELECT schema_name FROM restore_quarantine WHERE request_key=?", ("replace-corrupt",)
        ).fetchone()[0]
        assert quarantine != schema and quarantine.startswith("swarm_quarantine_")
        assert connection.raw.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (quarantine,)
        ).fetchone()
        connection.raw.execute(f'DROP SCHEMA "{quarantine}" CASCADE')


def test_real_pg_replace_rolls_back_live_schema_when_import_data_is_invalid(registries, tmp_path):
    import hashlib

    make, _ = registries
    registry = make()
    fixture = running(registry)
    snapshot = tmp_path / "snapshot"
    fixture.store.backup(snapshot)
    path = snapshot / "tasks.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(rows[0])
    header["columns"][0] = "not_a_column"
    rows[0] = json.dumps(header)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["tasks.jsonl"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SwarmStoreError, match="columns"):
        registry.restore(snapshot, request_key="failed-replace", replace_existing=True)
    assert fixture.store.read_task(fixture.actor, "work")["state"] == "running"


def test_real_pg_delete_reconciles_commit_before_local_journal(registries, tmp_path, monkeypatch):
    from jarvis.swarm.lifecycle import StorageLifecycle
    from jarvis.swarm.registry_router import RegistryRouter
    from jarvis.swarm.store import TeamRegistry

    make, _ = registries
    registry = make()
    fixture = running(registry)
    fixture.store.user_transition("canceled")
    router = RegistryRouter(TeamRegistry(tmp_path / "swarm"))
    router.set_remote(registry)
    manager = StorageLifecycle(router.root, router)
    save = manager._save

    def fail_once(operation):
        if operation["phase"] == "done":
            monkeypatch.setattr(manager, "_save", save)
            raise SwarmStoreError("Injected journal failure after committed PG deletion")
        save(operation)

    monkeypatch.setattr(manager, "_save", fail_once)
    with pytest.raises(SwarmStoreError, match="journal failure"):
        manager.delete(fixture.team["id"], "delete-once")
    assert manager.delete(fixture.team["id"], "delete-once")["deleted"]
    with registry.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM teams").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM team_deletions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_pg_old_delete_retry_never_fences_a_restored_generation(
    registries, tmp_path, monkeypatch
):
    from tests.fakes.swarm_runtime import runtime

    make, _ = registries
    registry = make()
    fixture = running(registry)
    snapshot = tmp_path / "snapshot"
    fixture.store.backup(snapshot)
    service = runtime(tmp_path / "service")
    service.registry.set_remote(registry)
    manager = service._storage_lifecycle()
    save = manager._save

    def fail_once(operation):
        if operation["kind"] == "delete" and operation["phase"] == "done":
            monkeypatch.setattr(manager, "_save", save)
            raise SwarmStoreError("Injected lost local deletion receipt")
        save(operation)

    try:
        monkeypatch.setattr(manager, "_save", fail_once)
        with pytest.raises(SwarmStoreError, match="lost local"):
            await service.delete_team(fixture.team["id"], "original-delete")
        registry.restore(snapshot, request_key="new-generation")
        assert registry.open(fixture.team["id"]).get()["state"] == "paused"
        assert (await service.delete_team(fixture.team["id"], "original-delete"))["deleted"]
        assert registry.open(fixture.team["id"]).get()["state"] == "paused"
    finally:
        service.registry.remote = None  # The fixture owns remote teardown.
        await service.stop()


def test_real_pg_quarantine_retention_is_scoped_and_keeps_replacement(registries, tmp_path):
    from contextlib import closing

    from jarvis.swarm.lifecycle import StorageLifecycle
    from jarvis.swarm.registry_router import RegistryRouter
    from jarvis.swarm.store import TeamRegistry

    make, _ = registries
    registry = make()
    fixture = running(registry)
    router = RegistryRouter(TeamRegistry(tmp_path / "swarm"))
    router.set_remote(registry)
    manager = StorageLifecycle(router.root, router)
    backup = manager.backup(fixture.team["id"], "before-replace")
    path = manager.backup_file(fixture.team["id"], backup["backup_id"])
    operation = manager.prepare_restore(path, "replace-and-prune", fixture.team["id"])
    manager.fence_restore(operation)
    restored = manager.apply_restore(operation)
    with registry.database.transaction() as connection:
        quarantine = connection.execute(
            "SELECT schema_name FROM restore_quarantine WHERE request_key=?", (operation["id"],)
        ).fetchone()[0]
    with closing(manager._journal()) as connection, connection:
        saved = json.loads(
            connection.execute(
                "SELECT record FROM operations WHERE id=?", (operation["id"],)
            ).fetchone()[0]
        )
        saved["created_at"] = 1
        connection.execute(
            "UPDATE operations SET record=? WHERE id=?", (json.dumps(saved), operation["id"])
        )
    store = registry.open(fixture.team["id"])
    controller = store.acquire_controller("retention-owner")
    assert manager.retain(fixture.team["id"], 30, controller)["quarantined_workspaces"] == "1"
    with registry.database.transaction() as connection:
        assert not connection.raw.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (quarantine,)
        ).fetchone()
    assert registry.open(restored["team"]["id"]).get()["state"] == "paused"


@pytest.mark.parametrize("damage", ["version", "hash", "columns", "duplicate", "state", "type"])
def test_distributed_inner_validation_precedes_any_live_fence(registries, tmp_path, damage):
    import hashlib
    import zipfile

    from jarvis.swarm.lifecycle import StorageLifecycle
    from jarvis.swarm.registry_router import RegistryRouter
    from jarvis.swarm.store import TeamRegistry

    make, _ = registries
    registry = make()
    fixture = running(registry)
    router = RegistryRouter(TeamRegistry(tmp_path / "swarm"))
    router.set_remote(registry)
    manager = StorageLifecycle(router.root, router)
    backup = manager.backup(fixture.team["id"], "valid-export")
    with zipfile.ZipFile(manager.backup_file(fixture.team["id"], backup["backup_id"])) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(payloads["team/manifest.json"])
    if damage == "version":
        manifest["schema_version"] = 999
    elif damage == "hash":
        manifest["files"]["tasks.jsonl"] = "0" * 64
    else:
        lines = payloads["team/tasks.jsonl"].decode().splitlines()
        header = json.loads(lines[0])
        if damage == "columns":
            header["columns"][0] = "unexpected_column"
            lines[0] = json.dumps(header)
        elif damage == "duplicate":
            lines.append(lines[1])
            manifest["counts"]["tasks"] = str(int(manifest["counts"]["tasks"]) + 1)
        else:
            row = json.loads(lines[1])
            if damage == "state":
                row[header["columns"].index("state")] = "invalid-state"
                row[header["columns"].index("record")]["state"] = "invalid-state"
            else:
                row[header["columns"].index("fence")] = "invalid-number"
            lines[1] = json.dumps(row)
        payloads["team/tasks.jsonl"] = ("\n".join(lines) + "\n").encode()
        manifest["files"]["tasks.jsonl"] = hashlib.sha256(payloads["team/tasks.jsonl"]).hexdigest()
    payloads["team/manifest.json"] = json.dumps(manifest).encode()
    envelope = json.loads(payloads["archive.json"])
    for name, content in payloads.items():
        if name != "archive.json":
            envelope["files"][name] = {
                "size": str(len(content)),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
    payloads["archive.json"] = json.dumps(envelope).encode()
    attacked = tmp_path / "damaged.zip"
    with zipfile.ZipFile(attacked, "w") as archive:
        for name, content in payloads.items():
            archive.writestr(name, content)
    with pytest.raises(SwarmStoreError):
        manager.prepare_restore(attacked, "invalid-restore", fixture.team["id"])
    assert fixture.store.get()["state"] == "running"
    assert fixture.store.read_task(fixture.actor, "work")["state"] == "running"


def test_real_pg_jsonb_rls_controller_fencing_and_exact_reservations(registries):
    make, clock = registries
    registry = make()
    fixture = running(registry, limits=BudgetLimits(token_budget="10000000000"))  # noqa: S106
    store, actor = fixture.store, fixture.actor
    with store._tx() as connection:
        assert (
            connection.raw.execute("SELECT pg_typeof(record)::text FROM team").fetchone()[0]
            == "jsonb"
        )
        connection.raw.execute("SELECT set_config('swarm.team_id',%s,true)", ("f" * 32,))
        assert connection.execute("SELECT record FROM team").fetchone() is None
    with pytest.raises(SwarmAccessError):
        registry.open(store.team_id, "unrelated-owner")
    second = running(registry)
    with pytest.raises(SwarmAccessError):
        second.store.read_task(actor, "work")
    with ThreadPoolExecutor(max_workers=12) as pool:

        def reserve(index):
            try:
                return store.reserve(actor, str(index), "1000000000", "0")
            except SwarmBudgetError:
                return None

        reservations = list(pool.map(reserve, range(12)))
    assert sum(item is not None for item in reservations) == 10
    assert store.get()["tokens_reserved"] == "10000000000"
    clock.now += 91
    replacement = store.acquire_controller("controller-b")
    assert replacement.fence > fixture.controller.fence
    with pytest.raises(SwarmAccessError):
        store.reserve(actor, "stale", "1", "0")
    with pytest.raises(SwarmAccessError):
        store.renew_controller(fixture.controller)


def test_real_pg_claim_race_and_creation_replay(registries):
    make, _ = registries
    registry = make()
    spec = TeamCreate(
        name="Replay",
        goal="No duplicate teams",
        mode="distributed",
        request_key="same",
        tasks=[task()],
    )
    with ThreadPoolExecutor(max_workers=6) as pool:
        teams = list(pool.map(lambda _: registry.create(spec), range(6)))
    assert len({team["id"] for team in teams}) == 1
    store = registry.open(teams[0]["id"])
    controller = store.acquire_controller("controller")
    store.transition(controller, "running")
    actors = [store.add_agent(controller, str(index)) for index in range(8)]

    def claim(actor):
        try:
            return store.claim(controller, actor.agent_id, "work")
        except SwarmConflictError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(claim, actors))
    assert sum(actor is not None for actor in claimed) == 1
    with store._tx() as connection:
        assert (
            connection.execute("SELECT count(*) FROM attempts WHERE state='running'").fetchone()[0]
            == 1
        )


def test_real_delivery_rebuilds_lost_stream_and_deduplicates_after_ack(registries):
    make, clock = registries
    fixture = running(make())
    registry, store = fixture.registry, fixture.store
    assert store.pump_delivery() > 0
    original = registry.delivery.poll(store, "consumer-a")
    assert original
    # Loss of exactly this team's stream models a Redis restart with lost memory.
    registry.delivery.client().delete(registry.delivery.stream(store.team_id))
    clock.now += 31
    assert store.pump_delivery() == len(original)
    recovered = registry.delivery.poll(store, "consumer-b")
    assert {hint.event_id for hint in recovered} == {hint.event_id for hint in original}
    for hint in recovered:
        registry.delivery.ack(store, hint)
    clock.now += 31
    assert store.pump_delivery() == 0
    assert registry.delivery.poll(store, "consumer-b") == []


def test_real_pg_mailbox_artifact_acceptance_and_snapshot_restore(registries, tmp_path):
    make, _ = registries
    fixture = running(make())
    store, actor = fixture.store, fixture.actor
    peer = store.add_agent(fixture.controller, "Peer")
    message = PeerMessage(
        request_key="progress",
        task_id="work",
        intent="SHARE_FINDING",
        summary="Verified fact",
        recipients=[peer.agent_id],
    )
    result = store.send_message(actor, message)
    assert store.send_message(actor, message)["id"] == result["id"]
    received = store.messages(peer)
    assert [item["id"] for item in received] == [result["id"]]
    store.ack_message(peer, result["id"])
    evidence = store.capture_result(fixture.controller, actor, "Result", "checked", "result")
    assert store.read_artifact(actor, evidence["id"])["content"] == "checked"
    accepted_result(fixture, actor, result="checked", evidence=[evidence["id"]])
    assert store.ready_tasks() == []
    assert store.records("reputation")
    assert json.dumps(store.world())
    backup = tmp_path / "backup"
    store.backup(backup)
    restored_registry = make()
    restored = restored_registry.restore(backup, request_key="restore")
    assert restored["id"] == store.team_id
    restored_store = restored_registry.open(store.team_id)
    assert restored_store.download_artifact(evidence["id"])[1] == b"checked"
    with pytest.raises(SwarmAccessError):
        restored_store.validate_actor(actor)
    with pytest.raises(SwarmAccessError):
        restored_store.renew_controller(fixture.controller)
    assert restored_store.records("reputation") == store.records("reputation")


def test_real_database_rolls_back_failed_mutation(registries):
    make, _ = registries
    fixture = running(make())
    with pytest.raises(SwarmStoreError):
        with fixture.store._tx(write=True) as connection:
            fixture.store._event(connection, "test.rollback", "Must not commit")
            raise RuntimeError("Injected controller failure")
    assert all(event["kind"] != "test.rollback" for event in fixture.store.events_after())


def test_real_s3_wire_artifact_and_immutable_duplicate(registries):
    make, _ = registries
    fixture = running(make(real_objects=True))
    fixture.registry.objects.check_connection()
    artifact = fixture.store.capture_result(
        fixture.controller, fixture.actor, "Wire proof", "437", "wire-result"
    )
    duplicate = fixture.store.capture_result(
        fixture.controller, fixture.actor, "Wire proof", "437", "wire-result"
    )
    assert duplicate["id"] == artifact["id"]
    assert fixture.store.download_artifact(artifact["id"])[1] == b"437"


def test_real_pg_add_tasks_checks_only_selected_existing_ids(registries):
    make, _ = registries
    fixture = running(make())
    created = fixture.store.add_tasks(fixture.controller, [task("next", dependencies=["work"])])
    assert created[0]["dependencies"] == ["work"]
    with pytest.raises(ValueError):
        fixture.store.add_tasks(fixture.controller, [task("missing", dependencies=["unknown"])])
    with pytest.raises(ValueError):
        fixture.store.add_tasks(
            fixture.controller, [task("a", dependencies=["b"]), task("b", dependencies=["a"])]
        )
    with pytest.raises(SwarmConflictError):
        fixture.store.add_tasks(fixture.controller, [task("next")])


def test_superuser_database_credentials_are_explicitly_refused():
    import psycopg

    from jarvis.swarm.distributed.database import PostgresDatabase

    with (
        psycopg.connect(
            host="127.0.0.1",
            port=55434,
            dbname="swarm",
            user="postgres",
            password="swarm-disposable-admin",  # noqa: S106 - disposable test service
        ) as connection,
        pytest.raises(SwarmAccessError, match="superuser/BYPASSRLS"),
    ):
        PostgresDatabase.validate_role(connection)


def test_distributed_operation_never_opens_sqlite(registries, monkeypatch):
    import sqlite3

    def denied(*args, **kwargs):
        pytest.fail("Distributed storage opened a SQLite connection")

    monkeypatch.setattr(sqlite3, "connect", denied)
    make, _ = registries
    fixture = running(make())
    assert fixture.store.get()["mode"] == "distributed"
    fixture.store.reserve(fixture.actor, "no-local-database", "25", "0")
    assert fixture.store.get()["tokens_reserved"] == "25"


def test_real_database_disconnect_rolls_back_uncommitted_event(registries):
    import psycopg

    make, _ = registries
    fixture = running(make())
    with pytest.raises(SwarmStoreError):
        with fixture.store._tx(write=True) as connection:
            fixture.store._event(connection, "test.disconnect", "Never committed")
            backend = connection.raw.execute("SELECT pg_backend_pid()").fetchone()[0]
            with psycopg.connect(
                host="127.0.0.1",
                port=55434,
                dbname="swarm",
                user="postgres",
                password="swarm-disposable-admin",  # noqa: S106 - disposable test service
            ) as admin:
                admin.execute("SELECT pg_terminate_backend(%s)", (backend,))
    assert all(event["kind"] != "test.disconnect" for event in fixture.store.events_after())


def test_pending_redis_delivery_is_claimed_after_consumer_failure(registries):
    make, _ = registries
    fixture = running(make())
    store, delivery = fixture.store, fixture.registry.delivery
    store.pump_delivery()
    hints = delivery.poll(store, "failed-consumer")
    assert hints
    # Advance only these disposable pending-entry idle ages. The next poll uses
    # the actual XAUTOCLAIM recovery command against Redis, with no fake broker.
    delivery.client().xclaim(
        delivery.stream(store.team_id),
        "controller",
        "failed-consumer",
        0,
        [hint.stream_id for hint in hints],
        idle=31_000,
    )
    recovered = delivery.poll(store, "replacement-consumer")
    assert {hint.event_id for hint in recovered} == {hint.event_id for hint in hints}


def test_repeatable_read_snapshot_does_not_block_writers(registries):
    make, _ = registries
    fixture = running(make())
    with fixture.store._tx() as connection, ThreadPoolExecutor(max_workers=1) as pool:
        before = fixture.store._team(connection)
        future = pool.submit(fixture.store.checkpoint, fixture.controller, {"step": "after"})
        after = future.result(timeout=2)
        assert after["version"] > before["version"]
        assert fixture.store._team(connection) == before
    assert fixture.store.get()["checkpoint"] == {"step": "after"}


def test_slow_redis_pump_does_not_hold_database_lock(registries):
    make, _ = registries
    fixture = running(make())
    delivery = fixture.registry.delivery
    original = delivery.client()
    entered, release = threading.Event(), threading.Event()

    class SlowRedis:
        def xadd(self, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original.xadd(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(original, name)

    delivery._client = SlowRedis()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(fixture.store.pump_delivery)
        assert entered.wait(2)
        try:
            pool.submit(fixture.store.heartbeat, fixture.actor).result(timeout=2)
        finally:
            release.set()
        assert future.result(timeout=5) > 0
    delivery._client = original


def test_delivery_retention_and_deleted_stream_cleanup_are_bounded(registries):
    make, clock = registries
    fixture = running(make())
    delivery, store = fixture.registry.delivery, fixture.store
    store.pump_delivery()
    hints = delivery.poll(store, "retention")
    delivery.ack_many(store, hints)
    assert 0 < delivery.client().ttl(delivery.stream(store.team_id)) <= 7 * 86400
    clock.now += 1
    retained = store.retain(fixture.controller, clock.now)
    assert int(retained["acknowledged_delivery_hints"]) == len(hints)
    store.user_transition("canceled")
    fixture.registry.delete(store.team_id)
    assert delivery.client().exists(delivery.stream(store.team_id)) == 0


def test_slow_s3_reserves_quota_and_allows_pause_before_fenced_finalize(registries):
    make, _ = registries
    fixture = running(make(), policy=CapabilityPolicy(max_artifact_bytes="3"))
    objects = fixture.registry.objects
    original = objects.client()
    entered, release = threading.Event(), threading.Event()

    class SlowS3:
        def put_object(self, **kwargs):
            entered.set()
            assert release.wait(5)
            return original.put_object(**kwargs)

        def __getattr__(self, name):
            return getattr(original, name)

    objects._client = SlowS3()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            fixture.store.capture_result, fixture.controller, fixture.actor, "Slow", "abc", "slow"
        )
        assert entered.wait(2)
        try:
            with pytest.raises(SwarmBudgetError, match="pending"):
                fixture.store.capture_result(
                    fixture.controller, fixture.actor, "Other", "x", "other"
                )
            pool.submit(fixture.store.heartbeat, fixture.actor).result(timeout=2)
            pool.submit(fixture.store.user_transition, "paused").result(timeout=2)
        finally:
            release.set()
        with pytest.raises(SwarmAccessError):
            future.result(timeout=5)
    assert fixture.store.records("artifacts") == []
    with fixture.store._tx() as connection:
        assert (
            connection.execute(
                "SELECT sum(size_bytes) FROM object_uploads WHERE state<>'completed'"
            ).fetchone()[0]
            == 3
        )
    objects._client = original

    replacement = fixture.store.acquire_controller("replacement")
    fixture.store.transition(replacement, "running")
    actor = fixture.store.claim(replacement, fixture.member.agent_id, "work")
    pending = fixture.store.pending_uploads(replacement, actor)
    assert len(pending) == 1
    with pytest.raises(SwarmConflictError, match="original reserved"):
        fixture.store.recover_upload(replacement, actor, pending[0]["id"], "def")
    recovered = fixture.store.recover_upload(replacement, actor, pending[0]["id"])
    assert recovered["attempt_fence"] == actor.task_fence
    assert recovered["recovered_from"]["source_attempt"] == fixture.actor.task_fence
    assert (
        fixture.store.recover_upload(replacement, actor, pending[0]["id"])["id"] == recovered["id"]
    )
    assert (
        fixture.store.capture_result(replacement, actor, "Slow", "abc", "slow")["id"]
        == recovered["id"]
    )
    assert fixture.store.pending_uploads(replacement, actor) == []
    assert fixture.store.download_artifact(recovered["id"])[1] == b"abc"


def test_unknown_s3_upload_keeps_quota_and_same_key_can_recover(registries):
    make, _ = registries
    fixture = running(make(), policy=CapabilityPolicy(max_artifact_bytes="3"))
    objects = fixture.registry.objects
    original = objects.client()

    class LostResponse:
        def put_object(self, **kwargs):
            original.put_object(**kwargs)
            raise ConnectionError("Injected lost object-write response")

        def __getattr__(self, name):
            return getattr(original, name)

    objects._client = LostResponse()
    with pytest.raises(SwarmStoreError):
        fixture.store.capture_result(fixture.controller, fixture.actor, "Result", "abc", "same")
    with pytest.raises(SwarmBudgetError):
        fixture.store.capture_result(fixture.controller, fixture.actor, "Other", "x", "other")
    objects._client = original
    result = fixture.store.capture_result(
        fixture.controller, fixture.actor, "Result", "abc", "same"
    )
    assert fixture.store.download_artifact(result["id"])[1] == b"abc"
    with fixture.store._tx() as connection:
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
            == "3"
        )
