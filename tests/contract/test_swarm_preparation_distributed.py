"""Preparation uses the same real PostgreSQL authority and exact budget ledger."""

import os

import pytest

from jarvis.swarm.preparation import _Accounting, _begin, _finish, _launch, read
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError
from tests.contract.test_swarm_distributed import registries as _registries
from tests.integration.test_swarm_preparation import PLAN, QUESTIONS, answers, launch, spec

registries = _registries

pytestmark = pytest.mark.skipif(
    os.environ.get("SWARM_DISTRIBUTED_TEST") != "1",
    reason="Explicit disposable PostgreSQL/Redis services are unavailable",
)


def test_postgres_preparation_accounting_and_exact_atomic_launch(registries):
    make, _ = registries
    registry = make()
    team = registry.create(
        spec().model_copy(update={"preparation_required": True, "mode": "distributed"})
    )
    store = registry.open(team["id"])
    assert read(store)["revision"] == 0
    controller = store.acquire_controller("owner-controller")
    operation = _begin(store, controller, "questions", "", None)
    accounting = _Accounting(store, controller, operation)
    reservation = accounting.reserve(accounting.actor, "billable", "100", "20")
    accounting.reconcile(controller, reservation["id"], "31", "6")
    accounting.reconcile(controller, reservation["id"], "31", "6")
    assert store.get()["tokens_used"] == "31"
    assert store.get()["tokens_reserved"] == "0"
    assert store.records("tasks") == []
    with pytest.raises(SwarmAccessError):
        store.reserve(accounting.actor, "worker-bypass", "100")
    questions = _finish(store, controller, operation, QUESTIONS)
    ready = _finish(
        store, controller, _begin(store, controller, "answers", "", answers(questions)), PLAN
    )
    with pytest.raises(SwarmConflictError):
        store.user_transition("running")
    approved = _launch(store, controller, launch(ready), 1000)
    assert approved["team"]["state"] == "running" and len(store.records("tasks")) == 5
    assert _launch(store, controller, launch(ready), 1000)["state"] == "launched"
    assert store.get()["tokens_used"] == "31"


def test_postgres_restore_retains_unlaunched_plan_and_changes_generation(registries, tmp_path):
    make, _ = registries
    registry = make()
    team = registry.create(
        spec().model_copy(update={"preparation_required": True, "mode": "distributed"})
    )
    store = registry.open(team["id"])
    controller = store.acquire_controller("owner-controller")
    questions = _finish(
        store, controller, _begin(store, controller, "questions", "", None), QUESTIONS
    )
    ready = _finish(
        store, controller, _begin(store, controller, "answers", "", answers(questions)), PLAN
    )
    store.backup(tmp_path / "backup")
    restored = registry.restore(tmp_path / "backup", request_key="restore", replace_existing=True)
    assert restored["state"] == "created"
    current = registry.open(team["id"])
    saved = read(current)
    assert saved["plan"] == ready["plan"] and saved["digest"] == ready["digest"]
    replacement = current.acquire_controller("new-owner")
    with pytest.raises(SwarmConflictError):
        _launch(current, replacement, launch(ready), 1000)
    assert _launch(current, replacement, launch(saved), 1000)["state"] == "launched"
