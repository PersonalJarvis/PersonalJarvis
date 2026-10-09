"""Steering reuses effects without treating low-risk sends as reads or consent."""

import json

import pytest

from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from tests.fakes.fake_subscription_session import SafeMessageGateway


@pytest.fixture
def runtime(tmp_path):
    ledger = LiveLedger(tmp_path / "receipts.db")
    tools = LiveTools(SafeMessageGateway(), ledger, "call", language="en", backend_model="")
    yield tools
    ledger.close()


async def test_internal_message_is_an_effect_even_when_permission_tier_is_safe(runtime):
    runtime.revision = 2
    args = {"name": "message_agent", "arguments_json": '{"target":"Alpha","message":"Hi"}'}
    assert runtime.steering_replay_key("call_tool", args) is not None
    result = await runtime.execute("obsolete", "call_tool", args, 1)
    assert result["status"] == "superseded"
    assert not runtime.gateway.calls


def test_replay_identity_uses_canonical_tool_target_and_payload(runtime):
    runtime.declarations()
    alias = next(alias for alias, name in runtime._names.items() if name == "message_agent")
    args = {"target": "Alpha", "message": "Run tests"}
    wrapped = {"name": "message_agent", "arguments_json": json.dumps(args)}
    assert runtime.steering_replay_key(alias, args) == runtime.steering_replay_key(
        "call_tool", wrapped,
    )
    assert runtime.steering_replay_key(alias, args) != runtime.steering_replay_key(
        alias, {**args, "target": "Beta"},
    )
    assert runtime.steering_replay_key("workspace-orchestrate", {"action": "inspect"}) is None
    assert runtime.steering_replay_key("workspace-orchestrate", {"action": "send"}) is not None


@pytest.mark.parametrize("name", ["confirm_action", "end_call", "discover_tools"])
def test_steering_never_reuses_approval_or_session_control(runtime, name):
    assert runtime.steering_replay_key(name, {}) is None
    assert runtime.steering_replay_key("call_tool", {
        "name": name, "arguments_json": "{}",
    }) is None


@pytest.mark.parametrize("name", ["confirm_action", "end_call"])
async def test_receipt_replay_cannot_forge_consent(runtime, name):
    result = await runtime.execute("attempt", name, {}, 0, replay_result={"success": True})
    assert result["success"] is False and result["executed"] is False
    assert not runtime.end_requested and not runtime.gateway.calls


async def test_reused_result_is_durable_and_call_id_cannot_change_target(runtime):
    args = {"target": "Alpha", "message": "Run tests"}
    first = await runtime.execute("original", "message_agent", args, 0)
    runtime.revision = 1
    replay = await runtime.execute("replanned", "message_agent", args, 1, replay_result=first)
    assert replay["reused_receipt"]
    assert runtime.ledger.operation("call", "replanned") == replay
    assert len(runtime.gateway.calls) == 1
    mismatch = await runtime.execute("replanned", "message_agent", {**args, "target": "Beta"}, 1)
    assert not mismatch["success"]
    assert len(runtime.gateway.calls) == 1
