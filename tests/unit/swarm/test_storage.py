"""Durability, exact quotas, fencing, graph and evidence behavior."""

# Exact token budgets are test quantities, not credentials.
# ruff: noqa: S106

import json
import multiprocessing
import sqlite3
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace

import pytest

from jarvis.core.swarm_types import BudgetLimits, CapabilityPolicy, TeamCreate
from jarvis.swarm.reputation import initial_profile, observe
from jarvis.swarm.store import (
    SCHEMA_VERSION,
    SwarmAccessError,
    SwarmBudgetError,
    SwarmConflictError,
    SwarmStoreError,
    TeamRegistry,
    TeamStore,
)
from tests.fakes.swarm_storage import accepted_result, running_team, task


def test_empty_registry_is_lazy_and_creation_is_idempotent(tmp_path):
    registry = TeamRegistry(tmp_path / "swarm")
    assert registry.list() == []
    assert not registry.root.exists()
    spec = TeamCreate(name="Team", goal="Goal", request_key="create")
    first = registry.create(spec)
    assert registry.create(spec) == first
    assert first["acceptance"] == "Goal"
    with pytest.raises(SwarmConflictError):
        registry.create(spec.model_copy(update={"limits": BudgetLimits(token_budget="1")}))
    assert len(registry.list()) == 1


def test_duplicate_create_race_has_one_durable_team(tmp_path):
    registry = TeamRegistry(tmp_path / "swarm")
    spec = TeamCreate(name="Team", goal="Goal", request_key="same")
    start = threading.Barrier(6, timeout=20)

    def create(_):
        start.wait()
        return registry.create(spec)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(create, range(6)))
    assert len({item["id"] for item in results}) == 1
    assert len(list(registry.root.glob("*/team.sqlite3"))) == 1


def _create_team_in_process(root, start):
    start.wait(timeout=20)
    return TeamRegistry(root).create(TeamCreate(name="Team", goal="Goal", request_key="same"))


@pytest.mark.parametrize("same_key", [True, False])
def test_saturated_creators_commit_without_waiting_for_outer_permits(
    tmp_path, monkeypatch, same_key
):
    registry = TeamRegistry(tmp_path / "swarm")
    registry.provision()
    catalog = registry.root / "catalog.sqlite3"
    start = threading.Barrier(16, timeout=5)
    opened = threading.local()
    connect = sqlite3.connect

    def synchronized_connect(path, *args, **kwargs):
        connection = connect(path, *args, **kwargs)
        if path == catalog and not getattr(opened, "catalog", False):
            opened.catalog = True
            try:
                start.wait()
            except BaseException:
                connection.close()
                raise
        return connection

    def create(index):
        return registry.create(
            TeamCreate(name="Team", goal="Goal", request_key="same" if same_key else str(index))
        )

    # Start every creator with its catalog connection open, before SQLite
    # serializes the writes. The winner must still open its nested team handle.
    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=16) as pool:
        patch.setattr(registry, "provision", lambda: None)  # Already provisioned above.
        patch.setattr(sqlite3, "connect", synchronized_connect)
        futures = [pool.submit(create, index) for index in range(16)]
        results = [future.result(timeout=8) for future in futures]
    expected = 1 if same_key else 16
    assert len({item["id"] for item in results}) == expected
    assert len(registry.list()) == expected
    assert len(list(registry.root.glob("*/team.sqlite3"))) == expected


def test_nested_connection_limit_fails_without_leaking_outer_permit(tmp_path):
    from contextlib import ExitStack

    from jarvis.swarm.store import _connection

    with ExitStack() as stack:
        for index in range(3):
            stack.enter_context(_connection(tmp_path / f"{index}.sqlite3"))
        with (
            pytest.raises(SwarmStoreError, match="nested connection limit"),
            _connection(tmp_path / "denied.sqlite3"),
        ):
            pytest.fail("An unbounded fourth connection was opened")
    assert not (tmp_path / "denied.sqlite3").exists()
    with _connection(tmp_path / "after.sqlite3") as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_duplicate_create_across_processes_has_one_durable_wal_team(tmp_path):
    root = tmp_path / "swarm"
    context = multiprocessing.get_context("spawn")
    with (
        context.Manager() as manager,
        ProcessPoolExecutor(max_workers=4, mp_context=context) as pool,
    ):
        start = manager.Barrier(4)
        futures = [pool.submit(_create_team_in_process, root, start) for _ in range(4)]
        results = [future.result(timeout=30) for future in futures]
    assert len({item["id"] for item in results}) == 1
    assert len(list(root.glob("*/team.sqlite3"))) == 1
    for path in (root / "catalog.sqlite3", root / results[0]["id"] / "team.sqlite3"):
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_normal_transactions_preserve_wal_and_allow_reader_during_write(tmp_path):
    fixture = running_team(tmp_path)
    with fixture.store._tx(write=True) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        connection.execute("UPDATE team SET record=json_set(record,'$.name','Pending')")
        assert fixture.store.get()["name"] == "Research"
    assert fixture.store.get()["name"] == "Pending"


@pytest.mark.parametrize(
    "graph",
    [
        [task("a", dependencies=["missing"])],
        [task("a", dependencies=["b"]), task("b", dependencies=["a"])],
        [task("a"), task("a")],
    ],
)
def test_invalid_graph_never_provisions_team(tmp_path, graph):
    registry = TeamRegistry(tmp_path / "swarm")
    with pytest.raises((ValueError, SwarmConflictError)):
        registry.create(TeamCreate(name="Team", goal="Goal", request_key="create", tasks=graph))
    assert not registry.root.exists()


def test_claim_race_allows_only_one_attempt(tmp_path):
    fixture = running_team(tmp_path)
    workers = [fixture.member] + [
        fixture.store.add_agent(fixture.controller, str(index)) for index in range(7)
    ]

    def claim(member):
        try:
            return fixture.store.claim(fixture.controller, member.agent_id, "work")
        except (SwarmConflictError, SwarmBudgetError):
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(claim, workers))
    assert sum(outcome is not None for outcome in outcomes) == 1
    with fixture.store._tx() as connection:
        assert (
            connection.execute("SELECT count(*) FROM attempts WHERE state='running'").fetchone()[0]
            == 1
        )


def test_foreign_controller_and_competing_controller_are_denied(tmp_path):
    a = running_team(tmp_path / "a")
    b = running_team(tmp_path / "b")
    assert a.store.acquire_controller("competitor") is None
    with pytest.raises(SwarmAccessError):
        a.store.add_agent(b.controller, "intruder")
    with pytest.raises(SwarmAccessError):
        a.store.add_agent(replace(a.controller, fence=a.controller.fence + 1), "intruder")


@pytest.mark.parametrize("action", ["paused", "canceled", "archived"])
def test_user_control_fences_other_instance_work_atomically(tmp_path, action):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.store.reserve(actor, "in-flight", "50")
    if action == "archived":
        fixture.store.user_transition("paused")
    result = fixture.registry.open(fixture.team["id"]).user_transition(action)
    assert result["state"] == action
    with pytest.raises(SwarmAccessError):
        fixture.store.heartbeat(actor)
    with pytest.raises(SwarmAccessError):
        fixture.store.renew_controller(fixture.controller)
    assert fixture.store.get()["tokens_reserved"] == "50"


def test_pause_resume_preserves_lead_and_retries_without_spending_attempt(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.store.user_transition("paused", expected_version=2)
    with pytest.raises(SwarmConflictError):
        fixture.store.user_transition("running", expected_version=2)
    fixture.store.user_transition("running", expected_version=3)
    controller = fixture.store.acquire_controller("second")
    renewed = fixture.store.claim(controller, fixture.member.agent_id, "work")
    assert renewed.task_fence > actor.task_fence
    assert fixture.store.read_task(renewed, "work")["attempt_count"] == 1
    assert fixture.store.get()["lead_id"] == fixture.team["lead_id"]


def test_graceful_release_preserves_running_team_and_revokes_all_handles(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    fixture.store.release_controller(fixture.controller)
    assert fixture.store.get()["state"] == "running"
    assert fixture.store.ready_tasks()[0]["attempt_count"] == 0
    for handle in (actor, lead):
        with pytest.raises(SwarmAccessError):
            fixture.store.validate_actor(handle)


def test_three_missed_heartbeats_recover_with_monotonic_fence(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.clock.now += 89
    fixture.store.renew_controller(fixture.controller)
    assert fixture.store.recover(fixture.controller) == []
    fixture.clock.now += 1
    recovered = fixture.store.recover(fixture.controller)
    assert recovered[0]["state"] == "ready"
    with pytest.raises(SwarmAccessError):
        fixture.store.heartbeat(actor)
    assert (
        fixture.store.claim(fixture.controller, fixture.member.agent_id, "work").task_fence
        > actor.task_fence
    )


def test_new_controller_recovers_prior_attempt_immediately(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.clock.now += 60
    fixture.store.heartbeat(actor)
    fixture.clock.now += 31
    replacement = fixture.store.acquire_controller("replacement")
    assert replacement is not None
    assert fixture.store.recover(replacement)[0]["state"] == "ready"
    with pytest.raises(SwarmAccessError):
        fixture.store.write_artifact(actor, "late", "late", "late")


def test_exact_reservations_exceed_sqlite_and_javascript_integer_ranges(tmp_path):
    maximum = 10**25
    fixture = running_team(
        tmp_path,
        limits=BudgetLimits(token_budget=str(maximum), monetary_limit_microusd=str(maximum)),
    )
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    reserved = fixture.store.reserve(actor, "request", maximum - 1, maximum - 2)
    assert fixture.store.reserve(actor, "request", maximum - 1, maximum - 2)["id"] == reserved["id"]
    with pytest.raises(SwarmBudgetError):
        fixture.store.reserve(actor, "over-budget", 2)
    fixture.store.reconcile(fixture.controller, reserved["id"], maximum - 3, maximum - 4)
    assert fixture.store.get()["tokens_used"] == str(maximum - 3)
    assert fixture.store.get()["cost_microusd"] == str(maximum - 4)
    assert fixture.store.get()["tokens_reserved"] == "0"
    fixture.store.reconcile(fixture.controller, reserved["id"], maximum - 3, maximum - 4)
    with pytest.raises(SwarmConflictError):
        fixture.store.reconcile(fixture.controller, reserved["id"], 1, 0)


def test_partial_usage_reconciliation_retains_only_unknown_dimension(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    reservation = fixture.store.reserve(actor, "request", 100, 50)
    fixture.store.reconcile(fixture.controller, reservation["id"], tokens=30)
    team = fixture.store.get()
    assert (team["tokens_reserved"], team["tokens_used"], team["cost_reserved_microusd"]) == (
        "0",
        "30",
        "50",
    )
    fixture.store.reconcile(fixture.controller, reservation["id"], tokens=30)
    assert fixture.store.get()["tokens_used"] == "30"
    assert (
        fixture.store.reconcile(fixture.controller, reservation["id"], cost_microusd=10)["state"]
        == "closed"
    )
    assert fixture.store.get()["cost_reserved_microusd"] == "0"


def test_unknown_usage_and_crash_keep_exposure(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    reservation = fixture.store.reserve(actor, "request", 100, 50)
    fixture.store.reconcile(fixture.controller, reservation["id"])
    fixture.store.release_controller(fixture.controller)
    reopened = fixture.registry.open(fixture.team["id"])
    assert reopened.get()["tokens_reserved"] == "100"
    assert reopened.get()["cost_reserved_microusd"] == "50"


def test_concurrent_quota_reservations_never_overbook(tmp_path):
    fixture = running_team(tmp_path, limits=BudgetLimits(token_budget="100"))
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")

    def reserve(index):
        try:
            return fixture.store.reserve(actor, str(index), 30)
        except SwarmBudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(result is not None for result in results) == 3
    assert fixture.store.get()["tokens_reserved"] == "90"


def test_usage_overrun_blocks_and_fences_remaining_work(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    reservation = fixture.store.reserve(actor, "request", 10)
    fixture.store.reconcile(fixture.controller, reservation["id"], 11, 0)
    assert fixture.store.get()["state"] == "blocked"
    with pytest.raises(SwarmAccessError):
        fixture.store.write_artifact(actor, "late", "late", "late")


def test_network_charge_is_exact_idempotent_and_attempt_bound(tmp_path):
    fixture = running_team(tmp_path, policy=CapabilityPolicy(max_network_bytes="100"))
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.store.consume_network(actor, "fetch", 40)
    assert fixture.store.consume_network(actor, "fetch", 40)["duplicate"]
    with pytest.raises(SwarmConflictError):
        fixture.store.consume_network(actor, "fetch", 39)
    fixture.store.fail(actor, "retry", controller=fixture.controller)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.store.consume_network(actor, "fetch", 40)
    assert fixture.store.get()["network_bytes"] == "80"
    with pytest.raises(SwarmBudgetError):
        fixture.store.consume_network(actor, "other", 21)


def test_failed_task_blocks_dependents_and_bounded_retries(tmp_path):
    fixture = running_team(
        tmp_path,
        tasks=[task("a"), task("b", dependencies=["a"]), task("c", dependencies=["b"])],
        limits=BudgetLimits(max_attempts=1),
    )
    with pytest.raises(SwarmConflictError):
        fixture.store.claim(fixture.controller, fixture.member.agent_id, "b")
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "a")
    fixture.store.fail(actor, "Final failure", controller=fixture.controller)
    assert {item["id"]: item["state"] for item in fixture.store.records("tasks")} == {
        "a": "failed",
        "b": "blocked",
        "c": "blocked",
    }


def test_accepted_result_replay_is_immutable_and_rates_once(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    result = accepted_result(fixture, actor)
    assert (
        fixture.store.finish(
            actor, result["result"], result["evidence"], {}, controller=fixture.controller
        )
        == result
    )
    assert len(fixture.store.records("reputation")) == 1
    with pytest.raises(SwarmConflictError):
        fixture.store.finish(
            actor, "different", result["evidence"], {}, controller=fixture.controller
        )


def test_accepted_dependency_evidence_keeps_original_attribution(tmp_path):
    fixture = running_team(tmp_path, tasks=[task("a"), task("b", dependencies=["a"])])
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "a")
    source = accepted_result(fixture, actor)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "b")
    direct = fixture.store.capture_result(fixture.controller, actor, "B", "B", "result-b")
    result = accepted_result(fixture, actor, evidence=[direct["id"], *source["evidence"]])
    assert result["evidence"][1] == source["evidence"][0]
    assert fixture.store.download_artifact(source["evidence"][0])[0]["task_id"] == "a"


@pytest.mark.parametrize(
    "mutation", [{"verifier_id": "self"}, {"contract_hash": "forged"}, {"quality": True}]
)
def test_forged_verification_does_not_grant_reputation(tmp_path, mutation):
    from jarvis.swarm.store import task_contract_hash

    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    artifact = fixture.store.write_artifact(actor, "Output", "content", "result")
    verdict = dict(
        accepted=True,
        verifier_id="trusted",
        kind="review",
        contract_hash=task_contract_hash(task()),
    )
    verdict.update(mutation)
    if verdict["verifier_id"] == "self":
        verdict["verifier_id"] = actor.agent_id
    with pytest.raises(SwarmAccessError):
        fixture.store.finish(
            actor, "Result", [artifact["id"]], verdict, controller=fixture.controller
        )
    assert fixture.store.records("reputation") == []


def test_trivial_farming_saturates_and_negative_evidence_is_bounded():
    farm = initial_profile()
    for _ in range(10000):
        farm, _ = observe(farm, difficulty=1, accepted=True)
    expert, _ = observe(None, difficulty=5, accepted=True)
    assert farm["credits"] < 2 < expert["credits"]
    assert farm["level"] < expert["level"]
    revised, delta = observe(expert, difficulty=5, accepted=False, reason="fabrication")
    assert -2 <= delta <= 0
    assert revised["reliability"] < expert["reliability"]
    assert revised["fabrications"] == "1"
    assert expert["fabrications"] == "0"


def test_duplicate_negative_evidence_does_not_double_penalize(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    accepted_result(fixture, actor)
    first = fixture.store.record_negative_evidence(
        fixture.controller, "work", "regression-1", "regression"
    )
    assert (
        fixture.store.record_negative_evidence(
            fixture.controller, "work", "regression-1", "regression"
        )
        == first
    )
    assert len(fixture.store.records("reputation")) == 2


def test_world_is_bounded_but_detailed_goal_remains_complete(tmp_path):
    fixture = running_team(tmp_path, acceptance="✓" * 10000)
    fixture.store.checkpoint(fixture.controller, {"large": "x" * 10000})
    for _index in range(10):
        fixture.store.append_event(
            fixture.controller, "activity", "✓" * 500, data={"blob": "✓" * 1000}
        )
    assert len(json.dumps(fixture.store.world(), ensure_ascii=True).encode()) <= 60000
    assert len(fixture.store.get()["acceptance"]) == 10000


def test_atomic_schema_migration_preserves_records_and_rejects_future(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    accepted = accepted_result(fixture, actor)
    with fixture.store._tx(write=True) as connection:
        connection.execute("PRAGMA user_version=1")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: fixture.store.check_schema(), range(4)))
    assert fixture.store.records("tasks")[0]["evidence"] == accepted["evidence"]
    assert (
        sum(event["kind"] == "storage.migrated" for event in fixture.store.records("events")) == 1
    )
    with fixture.store._tx(write=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(SwarmStoreError, match="schema"):
        fixture.registry.open(fixture.team["id"])
    assert fixture.store.records("tasks")[0]["result"] == accepted["result"]


def test_invalid_migration_rolls_back_version_and_new_schema_objects(tmp_path, monkeypatch):
    import jarvis.swarm.store as module

    fixture = running_team(tmp_path)
    with fixture.store._tx(write=True) as connection:
        connection.execute("PRAGMA user_version=1")

    def failing_migration(connection):
        connection.execute("CREATE TABLE partial_migration (id TEXT)")
        raise sqlite3.OperationalError("injected migration failure")

    monkeypatch.setattr(module, "_apply_schema", failing_migration)
    with pytest.raises(sqlite3.OperationalError):
        fixture.store.check_schema()
    with fixture.store._tx() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='partial_migration'"
            ).fetchone()
            is None
        )


def test_storage_handle_cannot_open_different_team_identity(tmp_path):
    fixture = running_team(tmp_path)
    forged = TeamStore(fixture.store.path, "f" * 32)
    with pytest.raises(SwarmAccessError):
        forged.check_schema()
