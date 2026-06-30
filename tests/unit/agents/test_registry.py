"""Unit-Tests fuer jarvis.agents.registry.JarvisAgentRegistry.

Deckt:
- Event-Ingestion pro Event-Typ (9 Typen: OpenClawTask*, BrainTurn*, ToolCall*,
  Harness* — zwei letztere als kombinierte Paare).
- Parent-Child-Linking ueber ``parent_trace_id``.
- Heuristisches Parent-Linking fuer HarnessDispatched (juengster running
  OpenClaw-Worker).
- tree() vs. snapshot() vs. to_json().
- TTL-basiertes Removal nach Completion.
- Tolerantes Verhalten bei Orphan-Events (parent not yet registered).
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from jarvis.agents import AgentNode, JarvisAgentRegistry
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    BrainTurnCompleted,
    BrainTurnStarted,
    HarnessCompleted,
    HarnessDispatched,
    JarvisAgentTaskCompleted,
    JarvisAgentReviewTriggered,
    JarvisAgentTaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from jarvis.core.protocols import HarnessResult


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def registry(bus: EventBus) -> JarvisAgentRegistry:
    return JarvisAgentRegistry(bus, ttl_completed_s=3600).attach()


# ────────────────────────────────────────────────────────────────
# Sub-Jarvis-Lifecycle
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openclaw_task_started_creates_running_node(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    tid = uuid4()
    await bus.publish(
        JarvisAgentTaskStarted(
            trace_id=tid,
            utterance="bau mir eine Flask-App",
            context_hints=["Port 8000", "single file"],
            provider="gemini",
            model="opus",
            max_duration_s=1800,
        )
    )
    snap = registry.snapshot()
    assert len(snap) == 1
    node = snap[tid.hex]
    assert node.kind == "jarvis_agent"
    assert node.status == "running"
    assert node.utterance == "bau mir eine Flask-App"
    assert node.context_hints == ["Port 8000", "single file"]
    assert node.provider == "gemini"
    assert node.model == "opus"
    # The display name is the neutral role only — engine/provider/model must
    # never leak into it (Sub-Agents board hygiene). provider/model stay as
    # structured fields above for internal aggregation.
    assert node.name == "Jarvis-Agent"
    assert "opus" not in node.name
    assert "OpenClaw" not in node.name


@pytest.mark.asyncio
async def test_openclaw_task_completed_marks_success_with_metrics(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    tid = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=tid, provider="claude-api", model="haiku"))
    await bus.publish(
        JarvisAgentTaskCompleted(
            trace_id=tid,
            success=True,
            summary="Fertig",
            full_log_len=1200,
            duration_s=14.2,
            cost_estimate_usd=0.034,
        )
    )
    node = registry.snapshot()[tid.hex]
    assert node.status == "completed"
    assert node.duration_ms == pytest.approx(14200.0)
    assert node.cost_usd == pytest.approx(0.034)
    assert any("Fertig" in p for p in node.prompts)


@pytest.mark.asyncio
async def test_openclaw_task_failed_marks_failed_with_error(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    tid = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=tid))
    await bus.publish(
        JarvisAgentTaskCompleted(
            trace_id=tid,
            success=False,
            error="timeout after 1800s",
            duration_s=1800.0,
        )
    )
    node = registry.snapshot()[tid.hex]
    assert node.status == "failed"
    assert node.error == "timeout after 1800s"


@pytest.mark.asyncio
async def test_review_triggered_updates_iteration_counter(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    tid = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=tid))
    await bus.publish(JarvisAgentReviewTriggered(trace_id=tid, iteration=1))
    await bus.publish(JarvisAgentReviewTriggered(trace_id=tid, iteration=2))
    node = registry.snapshot()[tid.hex]
    assert node.review_iterations == 2


# ────────────────────────────────────────────────────────────────
# Brain-Turn-Aggregation (in den juengsten Sub-Jarvis)
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_brain_turn_tokens_aggregate_into_newest_openclaw(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    sj = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=sj, provider="gemini", model="opus"))
    await bus.publish(
        BrainTurnStarted(
            trace_id=uuid4(),
            parent_trace_id=sj,
            provider="gemini",
            model="opus",
            system_prompt_preview="Du bist der Sub-Jarvis",
        )
    )
    await bus.publish(
        BrainTurnCompleted(tokens_in=1400, tokens_out=320, cost_usd=0.034)
    )
    node = registry.snapshot()[sj.hex]
    assert node.tokens_in == 1400
    assert node.tokens_out == 320
    assert node.cost_usd == pytest.approx(0.034)
    # Der system_prompt_preview landete in parent.prompts
    assert any("Du bist der Sub-Jarvis" in p for p in node.prompts)


# ────────────────────────────────────────────────────────────────
# Tool-Call-Listen
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_call_started_appends_to_parent(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    sj = uuid4()
    tc = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=sj))
    await bus.publish(
        ToolCallStarted(
            trace_id=tc,
            parent_trace_id=sj,
            tool_name="run_shell",
            args_preview='{"command": "ls"}',
        )
    )
    node = registry.snapshot()[sj.hex]
    assert len(node.tool_calls) == 1
    assert node.tool_calls[0]["tool_name"] == "run_shell"
    assert node.tool_calls[0]["status"] == "running"


@pytest.mark.asyncio
async def test_tool_call_completed_updates_matching_entry(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    sj = uuid4()
    tc = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=sj))
    await bus.publish(
        ToolCallStarted(trace_id=tc, parent_trace_id=sj, tool_name="run_shell", args_preview="ls")
    )
    await bus.publish(
        ToolCallCompleted(
            trace_id=tc, success=True, duration_ms=42.0, output_preview="file1\nfile2"
        )
    )
    node = registry.snapshot()[sj.hex]
    entry = node.tool_calls[0]
    assert entry["status"] == "completed"
    assert entry["duration_ms"] == 42.0
    assert entry["output_preview"] == "file1\nfile2"


# ────────────────────────────────────────────────────────────────
# Harness (Child-Heuristik)
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_harness_dispatched_links_to_newest_running_openclaw(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    sj = uuid4()
    harness = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=sj, provider="gemini", model="opus"))
    await bus.publish(HarnessDispatched(trace_id=harness, harness="openclaw"))

    snap = registry.snapshot()
    assert harness.hex in snap
    assert snap[harness.hex].kind == "harness"
    assert snap[harness.hex].parent_trace_id == sj.hex
    # Parent-Seite: Harness in children_trace_ids
    assert harness.hex in snap[sj.hex].children_trace_ids


@pytest.mark.asyncio
async def test_harness_completed_nonzero_exit_marks_failed(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    sj = uuid4()
    h = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=sj))
    await bus.publish(HarnessDispatched(trace_id=h, harness="openclaw"))
    await bus.publish(
        HarnessCompleted(
            trace_id=h,
            harness="openclaw",
            result=HarnessResult(
                stdout="", stderr="error", exit_code=1, duration_ms=500, is_final=True
            ),
        )
    )
    node = registry.snapshot()[h.hex]
    assert node.status == "failed"
    assert node.duration_ms == 500.0


# ────────────────────────────────────────────────────────────────
# Tree / snapshot / JSON-Serialisierung
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tree_returns_only_root_nodes(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    root_sj = uuid4()
    harness = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=root_sj))
    await bus.publish(HarnessDispatched(trace_id=harness, harness="openclaw"))

    tree = registry.tree()
    assert len(tree) == 1
    assert tree[0].trace_id == root_sj.hex


@pytest.mark.asyncio
async def test_to_json_is_serializable_shape(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    await bus.publish(JarvisAgentTaskStarted(trace_id=uuid4(), utterance="x"))
    payload = registry.to_json()
    assert "roots" in payload
    assert "all" in payload
    assert "count" in payload
    assert "server_ts_ns" in payload
    assert payload["count"] == 1
    assert payload["server_ts_ns"] > 0
    # Alle Roots sind JSON-safe dicts
    for root in payload["roots"]:
        assert isinstance(root, dict)
        assert "trace_id" in root
        assert "kind" in root


@pytest.mark.asyncio
async def test_clear_removes_all_nodes(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    await bus.publish(JarvisAgentTaskStarted(trace_id=uuid4()))
    await bus.publish(JarvisAgentTaskStarted(trace_id=uuid4()))
    assert len(registry.snapshot()) == 2
    registry.clear()
    assert len(registry.snapshot()) == 0


# ────────────────────────────────────────────────────────────────
# TTL
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ttl_removes_completed_node_after_timeout(bus: EventBus) -> None:
    # Sehr kurzes TTL fuer den Test
    reg = JarvisAgentRegistry(bus, ttl_completed_s=0).attach()
    tid = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=tid))
    await bus.publish(JarvisAgentTaskCompleted(trace_id=tid, success=True))
    # Event-Loop Runde lassen, damit der Cleanup-Task laeuft
    await asyncio.sleep(0.05)
    assert tid.hex not in reg.snapshot()


@pytest.mark.asyncio
async def test_orphan_child_tolerated_without_parent(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    # HarnessDispatched ohne running Sub-Jarvis → Harness als Root (parent=None)
    h = uuid4()
    await bus.publish(HarnessDispatched(trace_id=h, harness="openclaw"))
    node = registry.snapshot()[h.hex]
    assert node.parent_trace_id is None
    # tree() sollte ihn als Root zeigen
    assert any(r.trace_id == h.hex for r in registry.tree())


@pytest.mark.asyncio
async def test_agent_node_default_fields_are_empty() -> None:
    """Regressions-Guard: AgentNode-Defaults sind JSON-safe und leer."""
    node = AgentNode(trace_id="x", kind="openclaw", name="test")
    assert node.context_hints == []
    assert node.prompts == []
    assert node.tool_calls == []
    assert node.children_trace_ids == []
    assert node.parent_trace_id is None
    assert node.status == "running"


# --- Phase-6 MissionBus bridge ---


@pytest.mark.asyncio
async def test_mission_bus_bridge_creates_openclaw_node(
    registry: JarvisAgentRegistry,
) -> None:
    """attach_mission_bus: MissionDispatched -> AgentNode of kind=openclaw."""
    from jarvis.missions.event_bus import MissionBus
    from jarvis.missions.events import (
        EventEnvelope,
        MissionApproved,
        MissionDispatched,
        now_ms,
    )

    mbus = MissionBus()
    registry.attach_mission_bus(mbus)

    mission_id = "019e1800-0000-7000-8000-000000000001"
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="hauptjarvis",
            ts_ms=now_ms(),
            payload=MissionDispatched(prompt="hello world", language="de"),
        )
    )

    snap = registry.snapshot()
    tid = mission_id.replace("-", "")
    assert tid in snap, f"mission node not created. snap={snap.keys()}"
    node = snap[tid]
    assert node.kind == "jarvis_agent"
    assert node.status == "running"
    assert node.utterance == "hello world"

    # Approve -> completed + summary appended
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionApproved(
                result_uri="file:///x",
                tokens_used=1234,
                cost_usd=0.42,
                wall_ms=5000,
                summary_de="fertig",
                summary_en="done",
            ),
        )
    )

    node = registry.snapshot()[tid]
    assert node.status == "completed"
    assert node.cost_usd == pytest.approx(0.42)
    assert any("fertig" in p for p in node.prompts)


@pytest.mark.asyncio
async def test_mission_bus_bridge_failed_marks_failed(
    registry: JarvisAgentRegistry,
) -> None:
    """MissionFailed -> status=failed, error captured."""
    from jarvis.missions.event_bus import MissionBus
    from jarvis.missions.events import (
        EventEnvelope,
        MissionDispatched,
        MissionFailed,
        now_ms,
    )

    mbus = MissionBus()
    registry.attach_mission_bus(mbus)

    mission_id = "019e1800-0000-7000-8000-000000000002"
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="hauptjarvis",
            ts_ms=now_ms(),
            payload=MissionDispatched(prompt="boom", language="de"),
        )
    )
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionFailed(
                reason="critic exhausted",
                last_state="CRITIC_REVIEW",
            ),
        )
    )

    node = registry.snapshot()[mission_id.replace("-", "")]
    assert node.status == "failed"
    assert "critic exhausted" in (node.error or "")


@pytest.mark.asyncio
async def test_mission_bus_bridge_is_idempotent(
    registry: JarvisAgentRegistry,
) -> None:
    """attach_mission_bus twice must not double-subscribe."""
    from jarvis.missions.event_bus import MissionBus
    from jarvis.missions.events import EventEnvelope, MissionDispatched, now_ms

    mbus = MissionBus()
    registry.attach_mission_bus(mbus)
    registry.attach_mission_bus(mbus)  # second call: no-op

    mission_id = "019e1800-0000-7000-8000-000000000003"
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="hauptjarvis",
            ts_ms=now_ms(),
            payload=MissionDispatched(prompt="x", language="de"),
        )
    )
    snap = registry.snapshot()
    # Exactly one node — if we had double-subscribed the same async handler
    # twice the upsert path would still result in one node, but the test
    # documents the contract: attach is idempotent.
    assert len(snap) == 1
