"""Actual JSONB profile/routing/recheck identity parity on disposable services."""

# ruff: noqa: F811 - imported pytest fixture is injected by parameter name

import os

import pytest

from jarvis.swarm.recheck import _begin, _finish
from jarvis.swarm.store import SwarmConflictError
from tests.contract.test_swarm_distributed import registries, running  # noqa: F401
from tests.fakes.swarm_storage import accepted_result

pytestmark = pytest.mark.skipif(
    os.environ.get("SWARM_DISTRIBUTED_TEST") != "1",
    reason="Explicit disposable PostgreSQL/Redis services are unavailable",
)


def test_jsonb_domain_routing_and_measured_recheck_identity(registries):
    create, _ = registries
    fixture = running(create())
    accepted_result(fixture, fixture.actor)
    store = fixture.store
    profile = store.skill_profiles(fixture.member.agent_id)["profiles"][0]
    assert profile["acceptance"] == {"numerator": "1", "denominator": "1", "rate": 1.0}
    assert profile["rollback"]["denominator"] is None
    assert store.agents_for("general")[0]["id"] == fixture.member.agent_id
    record, task = _begin(store, "work")
    assert task is not None
    assert _begin(store, "work")[1] is None
    checked = _finish(store, record, "passed", "Deterministic fixture receipt", [])
    assert checked["state"] == "passed"
    assert _begin(store, "work")[1] is None
    profile = store.skill_profiles(fixture.member.agent_id)["profiles"][0]
    assert profile["regression"] == {"numerator": "0", "denominator": "1", "rate": 0.0}


def test_jsonb_recheck_generation_is_checked_before_rating_writes(registries):
    create, _ = registries
    fixture = running(create())
    accepted_result(fixture, fixture.actor)
    store = fixture.store
    record, _ = _begin(store, "work", "generation")
    with store._tx(write=True) as connection:
        team = store._team(connection)
        team["storage_generation"] = "replacement-generation"
        store._save_team(connection, team)
    with pytest.raises(SwarmConflictError, match="storage changed"):
        _finish(store, record, "regression", "Old generation result", [])
    assert not any(
        row["reason"] == "regression"
        for row in store.skill_profiles(fixture.member.agent_id)["history"]
    )
    resumed, task = _begin(store, "work", "generation")
    assert task is not None
    assert resumed["run_id"] != record["run_id"]
