"""Assignments transfer approved input, never source history or authority."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.core.swarm_types import BudgetLimits
from jarvis.swarm.specialists import assign, context, revoke
from jarvis.swarm.store import SwarmAccessError, SwarmBudgetError, SwarmConflictError
from tests.fakes.swarm_storage import running_team


def profile():
    return {
        "id": "personal-specialist",
        "name": "Research specialist",
        "title": "Researcher",
        "focus": ["Source evaluation", "Public data"],
        "state": "active",
    }


def test_assignment_persists_only_approved_projection_and_input(tmp_path):
    fixture = running_team(tmp_path)
    source = profile()
    before = copy.deepcopy(source)
    team = fixture.store.get()
    member = assign(
        fixture.store,
        fixture.controller,
        source,
        "Review this public table",
        "approved",
        team["version"],
    )
    assert member["id"] != source["id"]
    assert member["team_id"] == fixture.team["id"]
    assert member["source_agent_id"] == source["id"]
    assert member["role"] == "worker"
    assert member["state"] == "idle"
    assert member["level"] == 1
    assert member["verified_tasks"] == "0"
    assert "token" not in member and "token_hash" not in member
    assert source == before
    scoped = context(fixture.store, fixture.controller, member["id"])
    assert scoped == {
        "source_agent_id": source["id"],
        "name": source["name"],
        "title": source["title"],
        "focus": source["focus"],
        "authorized_input": "Review this public table",
    }
    scoped["focus"].append("Changed caller copy")
    assert context(fixture.store, fixture.controller, member["id"])["focus"] == source["focus"]
    after = fixture.store.get()
    assert after["version"] == team["version"] + 1
    assert {key: after[key] for key in ("goal", "acceptance", "policy", "limits")} == {
        key: team[key] for key in ("goal", "acceptance", "policy", "limits")
    }


@pytest.mark.parametrize("state", ["paused", "archived", "disabled", "running", ""])
def test_non_active_source_cannot_be_assigned(tmp_path, state):
    fixture = running_team(tmp_path)
    with pytest.raises(SwarmAccessError, match="active specialist"):
        assign(fixture.store, fixture.controller, profile() | {"state": state}, "", "request")


@pytest.mark.parametrize("field", ["history", "secrets", "workspace", "role", "tools", "token"])
def test_unapproved_profile_fields_never_enter_team_storage(tmp_path, field):
    fixture = running_team(tmp_path)
    with pytest.raises(ValueError, match="projected specialist"):
        assign(
            fixture.store, fixture.controller, profile() | {field: "Do not import"}, "", "request"
        )
    with fixture.store._tx() as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM agents").fetchone()[0] == 2


def test_duplicate_race_preserves_one_membership_and_rejects_changed_content(tmp_path):
    fixture = running_team(tmp_path)
    version = fixture.store.get()["version"]

    def run(_):
        return assign(fixture.store, fixture.controller, profile(), "Approved", "same", version)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(8)))
    assert len({record["id"] for record in results}) == 1
    assert fixture.store.get()["version"] == version + 1
    with fixture.store._tx() as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM agents").fetchone()[0] == 3
    with pytest.raises(SwarmConflictError, match="different assignment"):
        assign(fixture.store, fixture.controller, profile(), "Different input", "same")
    with pytest.raises(SwarmConflictError, match="different assignment"):
        assign(
            fixture.store, fixture.controller, profile() | {"title": "Other"}, "Approved", "same"
        )


def test_failed_commit_rolls_back_both_assignment_and_membership(tmp_path, monkeypatch):
    fixture = running_team(tmp_path)

    def fail_save(*args):
        raise OSError("Injected write failure")

    monkeypatch.setattr(fixture.store, "_save_team", fail_save)
    with pytest.raises(OSError, match="Injected"):
        assign(fixture.store, fixture.controller, profile(), "Approved", "same")
    with fixture.store._tx() as connection:
        assert connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM agents").fetchone()[0] == 2


def test_worker_limit_version_and_terminal_checks(tmp_path):
    fixture = running_team(tmp_path, limits=BudgetLimits(worker_limit="2"))
    version = fixture.store.get()["version"]
    with pytest.raises(SwarmConflictError, match="version changed"):
        assign(fixture.store, fixture.controller, profile(), "", "stale", version + 1)
    member = assign(fixture.store, fixture.controller, profile(), "", "first", version)
    with pytest.raises(SwarmBudgetError):
        assign(fixture.store, fixture.controller, profile(), "", "second")
    revoke(fixture.store, fixture.controller, member["id"])
    with pytest.raises(SwarmBudgetError):
        assign(fixture.store, fixture.controller, profile(), "", "third")
    fixture.store.transition(fixture.controller, "canceled")
    with pytest.raises(SwarmConflictError, match="Terminal"):
        assign(fixture.store, fixture.controller, profile(), "", "fourth")
    # A replay reports the existing stopped assignment without reactivating it.
    assert assign(fixture.store, fixture.controller, profile(), "", "first")["state"] == "stopped"


def test_cross_team_and_worker_credentials_cannot_assign_read_or_revoke(tmp_path):
    first = running_team(tmp_path / "first")
    second = running_team(tmp_path / "second")
    member = assign(first.store, first.controller, profile(), "Approved", "request")
    assert context(first.store, first.controller, first.member.agent_id) == {}
    for controller in (second.controller, first.member):
        with pytest.raises(SwarmAccessError):
            assign(first.store, controller, profile(), "", "other")
        with pytest.raises(SwarmAccessError):
            context(first.store, controller, member["id"])
        with pytest.raises(SwarmAccessError):
            revoke(first.store, controller, member["id"])
    with pytest.raises(SwarmAccessError):
        context(second.store, second.controller, member["id"])
    with pytest.raises(SwarmAccessError):
        revoke(first.store, first.controller, first.member.agent_id)
    with pytest.raises(SwarmAccessError):
        revoke(first.store, first.controller, first.team["lead_id"])


def test_revoke_fences_running_work_and_keeps_source_projection_unchanged(tmp_path):
    fixture = running_team(tmp_path)
    source = profile()
    before = copy.deepcopy(source)
    member = assign(fixture.store, fixture.controller, source, "Approved", "request")
    actor = fixture.store.claim(fixture.controller, member["id"], "work")
    result = revoke(fixture.store, fixture.controller, member["id"])
    assert result["state"] == "stopped"
    assert result["source_agent_id"] == source["id"]
    assert source == before
    with pytest.raises(SwarmAccessError):
        fixture.store.read_task(actor, "work")
    with pytest.raises(SwarmAccessError):
        context(fixture.store, fixture.controller, member["id"])
    with fixture.store._tx() as connection:
        task = json.loads(
            connection.execute("SELECT record FROM tasks WHERE id='work'").fetchone()[0]
        )
        assignment = json.loads(
            connection.execute(
                "SELECT record FROM decisions WHERE id=?", (member["id"],)
            ).fetchone()[0]
        )
    assert task["state"] == "ready"
    assert assignment["profile"] == {key: source[key] for key in ("id", "name", "title", "focus")}
