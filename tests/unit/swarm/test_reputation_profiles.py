"""Domain routing, uncertainty and measured profile contract regressions."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.core.swarm_reputation import AgentSkills, MeasuredRate
from jarvis.swarm.store import SwarmAccessError
from tests.fakes.swarm_storage import running_team, task


def test_domain_failures_outweigh_unrelated_success_without_granting_capabilities(tmp_path):
    fixture = running_team(tmp_path)
    store = fixture.store
    experienced = fixture.member.agent_id
    novice = store.add_agent(fixture.controller, "New generalist").agent_id
    math = dict(task("math", domain="math").model_dump(), team_id=fixture.team["id"])
    writing = dict(
        task("writing", domain="writing", difficulty=5).model_dump(), team_id=fixture.team["id"]
    )
    store.add_tasks(
        fixture.controller,
        [task("math", domain="math"), task("writing", domain="writing", difficulty=5)],
    )
    with store._tx(write=True) as connection:
        for index in range(12):
            store._rating(connection, experienced, writing, f"writing-{index}", accepted=True)
        for index in range(3):
            store._rating(connection, experienced, math, f"math-{index}", accepted=False)
    assert store.get_record("agents", experienced)["reliability"] > 0.9
    assert store.agents_for("math")[0]["id"] == novice
    assert store.agents_for("writing")[0]["id"] == experienced
    assert store.agents_for("novel")[0]["id"] == min(experienced, novice)
    assert store.agents_for("math", ["unapproved_tool"]) == []
    store.claim(fixture.controller, novice, "math")
    assert store.agents_for("math")[0]["id"] == experienced


def test_equal_posterior_prefers_uncertain_eligible_worker(tmp_path):
    fixture = running_team(tmp_path)
    novice = fixture.member.agent_id
    known = fixture.store.add_agent(fixture.controller, "Measured generalist").agent_id
    from jarvis.swarm.reputation import initial_profile

    profile = initial_profile()
    profile.update(alpha=100.0, beta=100.0, reliability=0.5, uncertainty=0.035, samples="196")
    with fixture.store._tx(write=True) as connection:
        connection.execute(
            "INSERT INTO reputation VALUES (?,?,?)", (known, "math", json.dumps(profile))
        )
    assert fixture.store.agents_for("math")[0]["id"] == novice


def test_unmeasured_rates_and_exact_counter_contract_survive_storage(tmp_path):
    fixture = running_team(tmp_path)
    record = fixture.store.skill_profiles(fixture.member.agent_id)
    model = AgentSkills.model_validate(record)
    assert model.profiles[0].acceptance.rate is None
    assert model.profiles[0].acceptance.denominator == "0"
    assert model.profiles[0].regression.denominator == "0"
    assert model.profiles[0].rollback.model_dump() == {
        "numerator": None,
        "denominator": None,
        "rate": None,
    }
    assert model.profiles[0].samples == "0"
    assert model.profiles[0].uncertainty > 0.2
    with pytest.raises(SwarmAccessError):
        fixture.store.skill_profiles("foreign-agent")
    with pytest.raises(ValidationError):
        MeasuredRate(numerator=1.5, denominator="2", rate=0.75)
    huge = "1000000000000000000000000000000"
    assert MeasuredRate(numerator=huge, denominator=huge, rate=1).numerator == huge
    # Wire field names are shared explicitly with the renderer, not coerced into JS numbers.
    types = Path("jarvis/ui/web/frontend/src/components/swarm/reputationTypes.ts").read_text()
    for name in (
        "numerator",
        "denominator",
        "rate",
        "samples",
        "verified_tasks",
        "acceptance",
        "regression",
        "rollback",
        "uncertainty",
        "difficulty_counts",
    ):
        assert name in types
