"""Parity guards for the shared Pipeline/Realtime turn planner."""

from __future__ import annotations

import pytest

from jarvis.brain.turn_planner import TurnPath, TurnReason, plan_turn
from jarvis.core.capabilities import Capability, CapabilityRegistry


@pytest.fixture
def registry() -> CapabilityRegistry:
    value = CapabilityRegistry()
    value.register(
        Capability(
            id="mcp.sap/customer_lookup",
            source="mcp",
            verbs=("lookup", "read"),
            objects=("sap", "customer"),
            description="Read one customer from SAP.",
            risk_tier="safe",
            requires_evidence=True,
        )
    )
    value.register(
        Capability(
            id="mcp.gmail/list_messages",
            source="mcp",
            verbs=("list", "read"),
            objects=("gmail", "inbox", "email"),
            description="Read messages from Gmail.",
            risk_tier="safe",
            requires_evidence=True,
        )
    )
    return value


@pytest.mark.parametrize(
    "utterance",
    [
        "What is the capital of France?",
        "Explain how DNS works.",
        "What is SAP?",
        "How do I open a file in Python?",
        "Tell me a joke.",
    ],
)
def test_timeless_or_instructional_turns_stay_native(
    utterance: str, registry: CapabilityRegistry,
) -> None:
    assert plan_turn(utterance, capability_registry=registry).path is TurnPath.NATIVE_REALTIME


@pytest.mark.parametrize(
    "utterance",
    [
        "What is in my Gmail inbox?",
        "Read the SAP customer record.",
        "Which pull requests are open today?",
        "What is the latest Python release?",
        "Who is my best friend?",
        "Which MCPs are connected?",
        "Use the morning routine skill.",
        "Spawn a Jarvis-Agent for this research.",
        "Call Anna.",
        "Click Save in the browser.",
    ],
)
def test_private_current_connected_and_action_turns_use_orchestrator(
    utterance: str, registry: CapabilityRegistry,
) -> None:
    plan = plan_turn(utterance, capability_registry=registry)
    assert plan.path is TurnPath.ORCHESTRATOR


def test_read_only_dynamic_connector_matches_live_capability(
    registry: CapabilityRegistry,
) -> None:
    plan = plan_turn("What customer is stored in SAP?", capability_registry=registry)
    assert plan.required_capabilities == ("mcp.sap/customer_lookup",)
    assert TurnReason.CONNECTED_DATA in plan.reasons
    assert plan.requires_evidence is True


def test_evidence_domain_routes_even_without_loaded_tool() -> None:
    plan = plan_turn(
        "Are there unread messages?",
        evidence_domains={"email": ("messages", "unread")},
    )
    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.CONNECTED_DATA in plan.reasons


def test_empty_turn_stays_native() -> None:
    assert plan_turn("   ").path is TurnPath.NATIVE_REALTIME
