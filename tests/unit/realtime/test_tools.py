"""Safety and provider-neutrality tests for realtime tool execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.realtime.tools import RealtimeToolBridge
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL


class FakeTool:
    def __init__(self, name: str = "open_app") -> None:
        self.name = name
        self.description = "Open a local application."
        self.schema = {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
            "required": ["app_name"],
        }
        self.risk_tier = "ask"

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("Realtime must never execute a tool directly")


class FakeExecutor:
    def __init__(self, *, confirmation_required: bool = False) -> None:
        self.confirmation_required = confirmation_required
        self.execute_calls = []
        self.confirmed_calls = []
        self.cancelled = []
        self.denied = []

    async def execute(self, tool, arguments, **kwargs):
        self.execute_calls.append((tool, arguments, kwargs))
        if self.confirmation_required:
            return SimpleNamespace(
                success=False,
                output={"tool_name": tool.name},
                error=VOICE_CONFIRM_SENTINEL,
            )
        return SimpleNamespace(success=True, output="opened", error=None)

    async def execute_confirmed(self, trace_id, **kwargs):
        self.confirmed_calls.append((trace_id, kwargs))
        return SimpleNamespace(success=True, output="confirmed", error=None)

    async def cancel_pending(self, trace_id):
        self.cancelled.append(trace_id)
        return True

    async def publish_guard_denied(self, name, reason, *, trace_id):
        self.denied.append((name, reason, trace_id))


@pytest.fixture
def wire_gateway():
    previous = runtime_refs.get_supervisor_tool_gateway()

    def _wire(brain):
        gateway = BrainSupervisorToolGateway(brain)
        runtime_refs.set_supervisor_tool_gateway(gateway)
        return gateway

    yield _wire
    runtime_refs.set_supervisor_tool_gateway(previous)


def _bridge(*, name: str = "open_app", confirmation_required: bool = False):
    tool = FakeTool(name)
    executor = FakeExecutor(confirmation_required=confirmation_required)
    bridge = RealtimeToolBridge(
        tools={name: tool}, executor=executor, language="en"
    )
    return bridge, tool, executor


def test_declaration_uses_a_cross_provider_safe_wire_name():
    bridge, _tool, _executor = _bridge(name="call-contact")

    declaration = bridge.declarations[0]

    assert declaration["name"].replace("_", "").isalnum()
    assert len(declaration["name"]) <= 64
    assert declaration["parameters"]["required"] == ["app_name"]


def test_supervisor_gateway_bridge_needs_no_classic_brain_argument(
    wire_gateway,
) -> None:
    tool = FakeTool("open_app")
    manager = SimpleNamespace(
        _tools={"open_app": tool},
        _tool_executor=FakeExecutor(),
    )
    wire_gateway(manager)

    bridge = RealtimeToolBridge.from_supervisor_gateway(language="en")

    assert bridge is not None
    assert [item["name"] for item in bridge.declarations] == ["open_app"]


@pytest.mark.asyncio
async def test_available_tool_runs_only_through_tool_executor():
    bridge, tool, executor = _bridge()
    await bridge.handle_user_transcript("Open Calculator")

    name, result = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )

    assert name == "open_app"
    assert result == {"success": True, "output": "opened", "error": None}
    assert executor.execute_calls[0][0] is tool
    assert executor.execute_calls[0][1] == {"app_name": "Calculator"}
    assert executor.execute_calls[0][2]["config_snapshot"]["voice_confirm"] is True


@pytest.mark.asyncio
async def test_vertex_toolset_prefix_resolves_to_the_declared_tool():
    """Vertex Live prefixes user-declared functions with the tool-set name.

    Live 2026-08-19 16:07: hybrid vertex-live called ``default:run_shell``.
    The bridge looked that name up verbatim, returned "Tool is not available
    in this session.", and the model spoke that error. The prefix is a
    transport artifact; the declared tool must still run.
    """
    from jarvis.realtime.tools import canonical_tool_wire_name

    assert canonical_tool_wire_name("default:run_shell") == "run_shell"
    assert canonical_tool_wire_name("open_app") == "open_app"
    assert canonical_tool_wire_name("default:jarvis_action") == "jarvis_action"

    bridge, tool, executor = _bridge()
    await bridge.handle_user_transcript("Open Calculator")

    name, result = await bridge.execute(
        wire_name="default:open_app", arguments={"app_name": "Calculator"}
    )

    assert name == "open_app"
    assert result == {"success": True, "output": "opened", "error": None}
    assert executor.execute_calls[0][0] is tool


@pytest.mark.asyncio
async def test_vertex_prefix_on_a_hashed_wire_name_still_runs():
    bridge, tool, executor = _bridge(name="call-contact")
    wire = bridge.declarations[0]["name"]
    await bridge.handle_user_transcript("Open Calculator")

    name, result = await bridge.execute(
        wire_name=f"default:{wire}", arguments={"app_name": "Calculator"}
    )

    assert name == "call-contact"
    assert result["success"] is True
    assert executor.execute_calls[0][0] is tool


@pytest.mark.asyncio
async def test_underscore_alias_runs_a_hyphenated_tool():
    """Live models drop the hash and call ``run_skill`` for ``run-skill``.

    Live 2026-08-20: that miss was "unknown realtime tool" and the user
    heard that the skill could not be loaded.
    """
    bridge, tool, executor = _bridge(name="run-skill")
    await bridge.handle_user_transcript("Open Calculator")

    name, result = await bridge.execute(
        wire_name="run_skill", arguments={"app_name": "Calculator"}
    )

    assert name == "run-skill"
    assert result["success"] is True
    assert executor.execute_calls[0][0] is tool

    name, result = await bridge.execute(
        wire_name="default:run_skill",
        arguments={"app_name": "Calculator"},
    )
    assert name == "run-skill"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_missing_required_argument_is_denied_before_executor():
    bridge, _tool, executor = _bridge()
    await bridge.handle_user_transcript("Open it")

    _name, result = await bridge.execute(wire_name="open_app", arguments={})

    assert result["success"] is False
    assert "Missing required" in result["error"]
    assert executor.execute_calls == []
    assert executor.denied


@pytest.mark.asyncio
async def test_clear_yes_resumes_the_original_pending_action_once():
    bridge, _tool, executor = _bridge(confirmation_required=True)
    await bridge.handle_user_transcript("Open Calculator")

    _name, first = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )
    await bridge.handle_user_transcript("Yes")
    _name, second = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Something else"}
    )

    assert first["confirmation_required"] is True
    assert second == {"success": True, "output": "confirmed", "error": None}
    assert len(executor.execute_calls) == 1
    assert len(executor.confirmed_calls) == 1


@pytest.mark.asyncio
async def test_clear_no_cancels_and_blocks_same_turn_retry():
    bridge, _tool, executor = _bridge(confirmation_required=True)
    await bridge.handle_user_transcript("Open Calculator")
    await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )

    await bridge.handle_user_transcript("No")
    _name, result = await bridge.execute(
        wire_name="open_app", arguments={"app_name": "Calculator"}
    )

    assert len(executor.cancelled) == 1
    assert result["success"] is False
    assert "declined" in result["error"]
    assert len(executor.execute_calls) == 1


def test_bridge_declarations_keep_the_full_json_schema() -> None:
    """Sanitizing is Gemini's wire-format concern: the provider-neutral
    bridge declarations (and thus the OpenAI path) keep the full schema,
    including additionalProperties."""
    tool = FakeTool("strict_tool")
    tool.schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "string"}},
    }
    executor = FakeExecutor()
    bridge = RealtimeToolBridge(
        tools={"strict_tool": tool}, executor=executor, language="en"
    )

    declaration = bridge.declarations[0]

    assert declaration["parameters"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_bridge_refreshes_replaced_brain_tools_and_denies_removed_tool(
    wire_gateway,
):
    executor = FakeExecutor()
    old_tool = FakeTool("old_tool")
    new_tool = FakeTool("new_tool")
    brain = SimpleNamespace(
        _tools={"old_tool": old_tool},
        _tool_executor=executor,
    )
    wire_gateway(brain)
    bridge = RealtimeToolBridge.from_brain(brain, language="en")
    assert bridge is not None

    brain._tools = {"new_tool": new_tool}

    assert bridge.refresh_from_source() is True
    assert [item["name"] for item in bridge.declarations] == ["new_tool"]
    _name, removed = await bridge.execute(
        wire_name="old_tool", arguments={"app_name": "Calculator"}
    )
    _name, added = await bridge.execute(
        wire_name="new_tool", arguments={"app_name": "Calculator"}
    )
    assert removed["success"] is False
    assert added["success"] is True


@pytest.mark.asyncio
async def test_bridge_refresh_retains_a_tool_awaiting_voice_confirmation(
    wire_gateway,
):
    executor = FakeExecutor(confirmation_required=True)
    pending_tool = FakeTool("pending_tool")
    brain = SimpleNamespace(
        _tools={"pending_tool": pending_tool},
        _tool_executor=executor,
    )
    wire_gateway(brain)
    bridge = RealtimeToolBridge.from_brain(brain, language="en")
    assert bridge is not None
    await bridge.handle_user_transcript("Open Calculator")
    await bridge.execute(
        wire_name="pending_tool", arguments={"app_name": "Calculator"}
    )

    brain._tools = {"new_tool": FakeTool("new_tool")}
    assert bridge.refresh_from_source() is True
    assert {item["name"] for item in bridge.declarations} == {
        "new_tool",
        "pending_tool",
    }

    await bridge.handle_user_transcript("Yes")
    _name, result = await bridge.execute(
        wire_name="pending_tool", arguments={"app_name": "Calculator"}
    )
    assert result["success"] is True
    assert bridge.refresh_from_source() is True
    assert [item["name"] for item in bridge.declarations] == ["new_tool"]


class FakeSpawnTool:
    """Minimal spawn_worker stand-in — no required args, monitor tier."""

    def __init__(self) -> None:
        self.name = "spawn_worker"
        self.description = "Delegates a heavy task to a background worker."
        self.schema = {"type": "object", "properties": {}, "required": []}
        self.risk_tier = "monitor"

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("Realtime must never execute a tool directly")


def _spawn_bridge():
    tool = FakeSpawnTool()
    executor = FakeExecutor()
    bridge = RealtimeToolBridge(
        tools={"spawn_worker": tool}, executor=executor, language="en"
    )
    return bridge, executor


@pytest.fixture(autouse=True)
def _fresh_spawn_offer_window():
    from jarvis.brain.spawn_gate import OFFER_WINDOW

    OFFER_WINDOW.disarm()
    yield
    OFFER_WINDOW.disarm()


@pytest.mark.asyncio
async def test_conversational_turn_blocks_realtime_spawn():
    """Explicit-delegation gate (mandate 2026-07-18): the realtime model chose
    spawn_worker on a plain conversational remark — blocked before the
    executor, with the redirect message fed back to the model."""
    bridge, executor = _spawn_bridge()
    await bridge.handle_user_transcript(
        "Ah, ich will gucken, wo ich als nächstes hinziehe."  # i18n-allow: live utterance
    )

    name, result = await bridge.execute(wire_name="spawn_worker", arguments={})

    assert name == "spawn_worker"
    assert result["success"] is False
    assert "did not explicitly ask" in result["error"]
    assert executor.execute_calls == []
    # A guard decision is NOT a broken tool. Without this flag the voice layer
    # reads the block as the turn's failure and speaks a stock line, throwing
    # away the real reason an earlier tool reported (live 2026-08-20 13:41:24 —
    # see tests/unit/realtime/test_multi_step_turn_is_finished.py).
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_explicit_agent_request_executes_realtime_spawn():
    bridge, executor = _spawn_bridge()
    await bridge.handle_user_transcript("Spawn an agent to research this.")

    _name, result = await bridge.execute(wire_name="spawn_worker", arguments={})

    assert result["success"] is True
    assert len(executor.execute_calls) == 1


@pytest.mark.asyncio
async def test_confirmed_delegation_offer_unlocks_realtime_spawn():
    """Blocked turn → the model offers delegation → a short yes unlocks it."""
    bridge, executor = _spawn_bridge()
    await bridge.handle_user_transcript("Figure out where I should move next.")
    _name, blocked = await bridge.execute(wire_name="spawn_worker", arguments={})
    assert blocked["success"] is False
    assert executor.execute_calls == []

    await bridge.handle_user_transcript("Yes, go ahead.")
    _name, result = await bridge.execute(wire_name="spawn_worker", arguments={})

    assert result["success"] is True
    assert len(executor.execute_calls) == 1


# ── the 2026-08-24 10:24 session: three asks, zero agents on the board ─────


@pytest.mark.asyncio
async def test_correcting_the_assistant_still_starts_the_worker():
    """Turn 4 of the live session, verbatim.

    The model had just offered to do the job itself. "No, no, which a worker
    should do it." is the user OVERRULING that offer and naming the vehicle,
    and it must reach the executor. It used to score as a spawn DECLINE — the
    English "no" read as the German "kein" — so the board stayed empty at
    "0 running · 0 in total" while the user asked three times.
    """
    bridge, executor = _spawn_bridge()
    await bridge.handle_user_transcript("No, no, which a worker should do it.")

    _name, result = await bridge.execute(wire_name="spawn_worker", arguments={})

    assert result["success"] is True
    assert len(executor.execute_calls) == 1


@pytest.mark.asyncio
async def test_spoken_confirmation_naming_the_vehicle_starts_the_worker():
    """Turn 5 of the live session: a confirmation refused for being 8 words."""
    bridge, executor = _spawn_bridge()
    await bridge.handle_user_transcript("Give me an overview of my calendar.")
    _name, blocked = await bridge.execute(wire_name="spawn_worker", arguments={})
    assert blocked["success"] is False

    await bridge.handle_user_transcript("Just follow the sub and then do it.")
    _name, result = await bridge.execute(wire_name="spawn_worker", arguments={})

    assert result["success"] is True
    assert len(executor.execute_calls) == 1


@pytest.mark.asyncio
async def test_a_real_decline_still_starts_nothing():
    """The guard this all protects must survive: an explicit no spawns nothing."""
    bridge, executor = _spawn_bridge()
    await bridge.handle_user_transcript(
        "No, do not spawn a subagent, talk to me directly."
    )

    _name, result = await bridge.execute(wire_name="spawn_worker", arguments={})

    assert result["success"] is False
    assert executor.execute_calls == []
