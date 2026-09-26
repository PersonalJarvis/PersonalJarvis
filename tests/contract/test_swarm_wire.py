"""Exact Swarm wire quantities and strict model boundary."""

import json

import pytest
from pydantic import ValidationError

from jarvis.core.swarm_types import BudgetLimits, PeerMessage, TeamCreate


@pytest.mark.parametrize("quantity", [10_000_000_000, 2**63, 10**30])
def test_large_counters_roundtrip_without_float_or_sqlite_integer_coercion(quantity):
    limits = BudgetLimits(token_budget=quantity, worker_limit=str(quantity))
    wire = json.loads(limits.model_dump_json())
    assert wire["token_budget"] == str(quantity)
    assert BudgetLimits.model_validate(wire).worker_limit == str(quantity)


@pytest.mark.parametrize("quantity", [True, -1, 1.5, "1e10", "1.0", "-2", "9" * 32])
def test_lossy_or_unbounded_counter_input_is_rejected(quantity):
    with pytest.raises(ValidationError):
        BudgetLimits(token_budget=quantity)


def test_peer_payload_cannot_carry_authority_fields():
    for injected in ({"budget": "10000000"}, {"sender": "lead"}, {"role": "scheduler"}):
        with pytest.raises(ValidationError):
            PeerMessage.model_validate(
                {
                    "intent": "SHARE_FINDING",
                    "task_id": "research",
                    "summary": "A relevant source",
                    "request_key": "finding-1",
                    **injected,
                }
            )


def test_team_defaults_are_opt_in_request_scoped_and_independent():
    a = TeamCreate(name="A", goal="Research A", request_key="a")
    b = TeamCreate(name="B", goal="Research B", request_key="b")
    a.policy.tools.clear()
    assert b.policy.tools
    assert a.mode == "local"
    assert a.tasks == []
