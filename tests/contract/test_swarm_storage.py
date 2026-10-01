"""The same local storage authorization contract runs on all three OSes."""

# SQL selectors are fixed test parameters; forged tokens are deliberately invalid.
# ruff: noqa: S608, S106

import json
import sqlite3
from dataclasses import replace

import pytest

from jarvis.core.swarm_types import (
    AgentRole,
    AgentState,
    PeerIntent,
    PeerMessage,
    TaskState,
    TeamState,
)
from jarvis.swarm.store import SwarmAccessError
from tests.fakes.swarm_storage import running_team


@pytest.mark.parametrize(
    "boundary",
    [
        "read",
        "write",
        "search",
        "events",
        "large-cursor-events",
        "download",
        "send",
        "mailbox",
        "subscribe",
        "discover",
        "reserve",
        "network",
        "tools",
        "tool-read",
        "heartbeat",
    ],
)
@pytest.mark.parametrize(
    "identity", ["foreign", "forged-team", "forged-token", "forged-attempt", "revoked", "ordinary"]
)
def test_storage_authorization_at_every_worker_boundary(tmp_path, boundary, identity):
    a = running_team(tmp_path / "a")
    b = running_team(tmp_path / "b")
    actor = a.store.claim(a.controller, a.member.agent_id, "work")
    artifact = a.store.write_artifact(actor, "Same object", "identical data", "same-key")
    other = b.store.claim(b.controller, b.member.agent_id, "work")
    b.store.write_artifact(other, "Same object", "identical data", "same-key")
    if identity == "foreign":
        actor = other
    elif identity == "forged-team":
        actor = replace(other, team_id=a.team["id"])
    elif identity == "forged-token":
        actor = replace(actor, token="invented")
    elif identity == "forged-attempt":
        actor = replace(actor, task_fence=actor.task_fence + 1)
    elif identity == "ordinary":
        actor = replace(actor, agent_id="ordinary-agent-with-no-membership")
    else:
        a.store.revoke_member(a.controller, actor.agent_id)
    operations = {
        "read": lambda: a.store.read_task(actor, "work"),
        "write": lambda: a.store.write_artifact(actor, "Denied", "data", "denied"),
        "search": lambda: a.store.search(actor, "identical", kind="artifacts"),
        "events": lambda: a.store.events_after(actor=actor),
        "large-cursor-events": lambda: a.store.events_after(str(10**25), actor=actor),
        "download": lambda: a.store.read_artifact(actor, artifact["id"]),
        "send": lambda: a.store.send_message(
            actor,
            PeerMessage(
                intent="COORD_STATUS", task_id="work", summary="data", request_key="denied"
            ),
        ),
        "mailbox": lambda: a.store.messages(actor),
        "subscribe": lambda: a.store.subscribe(actor, "topic"),
        "discover": lambda: a.store.discover(actor, "work"),
        "reserve": lambda: a.store.reserve(actor, "denied", 1),
        "network": lambda: a.store.consume_network(actor, "denied", 1),
        "tools": lambda: a.store.tool_catalog(actor),
        "tool-read": lambda: a.store.get_tool(actor, "guess"),
        "heartbeat": lambda: a.store.heartbeat(actor),
    }
    with pytest.raises(SwarmAccessError):
        operations[boundary]()


def test_owner_catalog_guesses_and_traversal_cannot_authorize_access(tmp_path):
    fixture = running_team(tmp_path)
    for team_id, owner in (
        (fixture.team["id"], "another-owner"),
        ("../team", "local-user"),
        ("0" * 32, "local-user"),
    ):
        with pytest.raises(SwarmAccessError):
            fixture.registry.open(team_id, owner)
    assert fixture.registry.list("another-owner") == []


def test_guessed_cross_team_evidence_and_recipients_are_denied(tmp_path):
    a = running_team(tmp_path / "a")
    b = running_team(tmp_path / "b")
    actor = a.store.claim(a.controller, a.member.agent_id, "work")
    other = b.store.claim(b.controller, b.member.agent_id, "work")
    foreign = b.store.write_artifact(other, "Private", "private content", "private")
    with pytest.raises(SwarmAccessError):
        a.store.read_artifact(actor, foreign["id"])
    with pytest.raises(SwarmAccessError):
        a.store.send_message(
            actor,
            PeerMessage(
                intent="SHARE_FINDING",
                task_id="work",
                summary="copy",
                request_key="foreign",
                evidence=[foreign["id"]],
            ),
        )
    with pytest.raises(SwarmAccessError):
        a.store.send_message(
            actor,
            PeerMessage(
                intent="SHARE_FINDING",
                task_id="work",
                summary="send",
                request_key="foreign",
                recipients=[other.agent_id],
            ),
        )
    assert a.store.search(actor, "private content", kind="artifacts") == []
    assert a.store.get()["tokens_used"] == b.store.get()["tokens_used"] == "0"


@pytest.mark.parametrize(
    "table,enum,column",
    [
        ("team", TeamState, None),
        ("tasks", TaskState, "state"),
        ("agents", AgentState, "state"),
        ("agents", AgentRole, "role"),
    ],
)
def test_sql_enum_contract_and_json_parity_are_enforced(tmp_path, table, enum, column):
    fixture = running_team(tmp_path)
    with fixture.store._tx() as connection:
        row = connection.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
        record = json.loads(row["record"])
    # Every Pydantic enum value is accepted by the corresponding SQL contract.
    field = column or "state"
    for value in enum:
        changed = dict(record, **{field: value.value})
        with fixture.store._tx(write=True) as connection:
            if column:
                connection.execute(
                    f"UPDATE {table} SET {column}=?,record=? WHERE id=?",
                    (value.value, json.dumps(changed), row["id"]),
                )
            else:
                connection.execute(f"UPDATE {table} SET record=?", (json.dumps(changed),))
    with pytest.raises(sqlite3.IntegrityError), fixture.store._tx(write=True) as connection:
        changed = dict(record, **{field: "invented-state"})
        if column:
            connection.execute(
                f"UPDATE {table} SET {column}=?,record=? WHERE id=?",
                ("invented-state", json.dumps(changed), row["id"]),
            )
        else:
            connection.execute(f"UPDATE {table} SET record=?", (json.dumps(changed),))


def test_sql_rejects_json_scope_and_state_that_disagree_with_index_columns(tmp_path):
    fixture = running_team(tmp_path)
    with pytest.raises(sqlite3.IntegrityError), fixture.store._tx(write=True) as connection:
        connection.execute("UPDATE agents SET state='running'")
    with pytest.raises(sqlite3.IntegrityError), fixture.store._tx(write=True) as connection:
        connection.execute("UPDATE tasks SET record=json_set(record,'$.team_id','foreign-team')")


def test_peer_intent_sql_enum_parity(tmp_path):
    fixture = running_team(tmp_path)
    for intent in PeerIntent:
        result = fixture.store.send_message(
            fixture.member,
            PeerMessage(intent=intent, task_id="work", summary="Message", request_key=intent.value),
        )
        assert result["intent"] == intent.value
    with pytest.raises(sqlite3.IntegrityError), fixture.store._tx(write=True) as connection:
        connection.execute("UPDATE messages SET intent='INCREASE_BUDGET'")
