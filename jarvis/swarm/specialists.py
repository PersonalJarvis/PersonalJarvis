"""Explicit specialist assignments import only an approved profile and input.

The source profile is projected by the trusted host before this boundary. It is
never an execution authority: the new identity is a worker in this team's own
membership table, with fresh credentials, policy and reputation.
"""

from __future__ import annotations

import json
from typing import Any

from jarvis.core.swarm_types import SwarmController
from jarvis.swarm.store import (
    SwarmAccessError,
    SwarmBudgetError,
    SwarmConflictError,
    TeamStore,
    _digest,
    _check_expected,
    _id,
    _json,
)

_KIND = "specialist_assignment"
_TERMINAL = frozenset({"succeeded", "failed", "canceled", "archived"})


def _text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field} must be text of at most {maximum} characters")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _profile(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"id", "name", "title", "focus", "state"}:
        raise ValueError("Only the projected specialist identity and focus are accepted")
    if value["state"] != "active":
        raise SwarmAccessError("Only an active specialist can be assigned")
    focus = value["focus"]
    if not isinstance(focus, list) or len(focus) > 20:
        raise ValueError("Specialist focus must contain at most 20 entries")
    return {
        "id": _text(value["id"], "source ID", 100),
        "name": _text(value["name"], "name", 120),
        "title": _text(value["title"], "title", 200, allow_empty=True),
        "focus": [_text(item, "focus", 200) for item in focus],
    }


def _assignment(store: TeamStore, connection: Any, agent_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT record FROM decisions WHERE id=?", (agent_id,)).fetchone()
    if row is None:
        raise SwarmAccessError("Specialist assignment is unavailable in this team")
    assignment = json.loads(row[0])
    if (
        assignment.get("kind") != _KIND
        or assignment.get("team_id") != store.team_id
        or assignment.get("agent_id") != agent_id
    ):
        raise SwarmAccessError("Specialist assignment is unavailable in this team")
    return assignment


def _member(connection: Any, assignment: dict[str, Any], *, active: bool) -> dict[str, Any]:
    row = connection.execute(
        "SELECT active,record FROM agents WHERE id=?", (assignment["agent_id"],)
    ).fetchone()
    if row is None or (active and not row[0]):
        raise SwarmAccessError("Specialist membership is unavailable or revoked")
    agent = json.loads(row[1])
    if (
        agent["team_id"] != assignment["team_id"]
        or agent["role"] != "worker"
        or agent.get("source_agent_id") != assignment["profile"]["id"]
    ):
        raise SwarmAccessError("Specialist membership does not match its approved assignment")
    return agent


def assign(
    store: TeamStore,
    controller: SwarmController,
    profile: dict[str, Any],
    authorized_input: str,
    request_key: str,
    expected_version: int | None = None,
    expected_storage_generation: str | None = None,
) -> dict[str, Any]:
    """Atomically create one scoped worker from an explicit user authorization."""
    approved_profile = _profile(profile)
    authorized_input = _text(authorized_input, "approved input", 20_000, allow_empty=True)
    request_key = _text(request_key, "request key", 100)
    if expected_version is not None and (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
    ):
        raise ValueError("Expected version must be a positive integer")
    content = {"profile": approved_profile, "authorized_input": authorized_input}
    key = f"{_KIND}:" + _digest(request_key)
    with store._tx(write=True) as connection:
        store._controller(connection, controller)
        _check_expected(store._team(connection), None, expected_storage_generation)
        previous = connection.execute(
            "SELECT record FROM decisions WHERE request_key=?", (key,)
        ).fetchone()
        if previous:
            assignment = json.loads(previous[0])
            if (
                assignment.get("kind") != _KIND
                or assignment.get("team_id") != store.team_id
                or any(assignment.get(field) != value for field, value in content.items())
            ):
                raise SwarmConflictError("Request key already identifies a different assignment")
            return _member(connection, assignment, active=False)
        team = store._team(connection)
        if team["state"] in _TERMINAL:
            raise SwarmConflictError("Terminal teams cannot assign specialists")
        if expected_version is not None and team["version"] != expected_version:
            raise SwarmConflictError("Team version changed before specialist assignment")
        count = connection.execute("SELECT count(*) FROM agents WHERE role<>'lead'").fetchone()[0]
        if count >= int(team["limits"]["worker_limit"]):
            raise SwarmBudgetError("Total logical worker limit reached")
        agent_id = _id()
        store._insert_agent(
            connection, agent_id, approved_profile["name"], "worker", "general", "delivery"
        )
        agent = json.loads(
            connection.execute("SELECT record FROM agents WHERE id=?", (agent_id,)).fetchone()[0]
        )
        agent["source_agent_id"] = approved_profile["id"]
        store._save_agent(connection, agent)
        assignment = {
            "id": agent_id,
            "kind": _KIND,
            "team_id": store.team_id,
            "agent_id": agent_id,
            **content,
            "created_at": store.clock(),
        }
        connection.execute(
            "INSERT INTO decisions (id,request_key,record) VALUES (?,?,?)",
            (agent_id, key, _json(assignment)),
        )
        team["version"] += 1
        store._save_team(connection, team)
        store._event(
            connection,
            "agent.specialist_assigned",
            "Specialist assigned with approved input",
            agent_id=agent_id,
            data={"source_agent_id": approved_profile["id"]},
        )
        return agent


def context(store: TeamStore, controller: SwarmController, agent_id: str) -> dict[str, Any]:
    """Return only the approved input and projection for a current membership."""
    with store._tx() as connection:
        store._controller(connection, controller)
        if connection.execute("SELECT 1 FROM decisions WHERE id=?", (agent_id,)).fetchone() is None:
            row = connection.execute(
                "SELECT record FROM agents WHERE id=? AND active=1", (agent_id,)
            ).fetchone()
            if row is None:
                raise SwarmAccessError("Active team member required")
            ordinary = json.loads(row[0])
            if ordinary["team_id"] != store.team_id or ordinary.get("source_agent_id"):
                raise SwarmAccessError("Specialist assignment is unavailable in this team")
            return {}
        assignment = _assignment(store, connection, agent_id)
        _member(connection, assignment, active=True)
        profile = assignment["profile"]
        return {
            "source_agent_id": profile["id"],
            "name": profile["name"],
            "title": profile["title"],
            "focus": profile["focus"],
            "authorized_input": assignment["authorized_input"],
        }


def revoke(store: TeamStore, controller: SwarmController, agent_id: str) -> dict[str, Any]:
    """Revoke only this team's assignment; never access the ordinary agent store."""
    with store._tx() as connection:
        store._controller(connection, controller)
        assignment = _assignment(store, connection, agent_id)
        _member(connection, assignment, active=False)
    store.revoke_member(controller, agent_id)
    with store._tx() as connection:
        store._controller(connection, controller)
        return _member(connection, assignment, active=False)
