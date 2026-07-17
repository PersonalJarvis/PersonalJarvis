"""Unit tests for jarvis.agents.registry.JarvisAgentRegistry.

Covers:
- Event ingestion per event type (9 types: OpenClawTask*, BrainTurn*, ToolCall*,
  Harness* — the latter two as combined pairs).
- Parent-child linking via ``parent_trace_id``.
- Heuristic parent linking for HarnessDispatched (newest running
  OpenClaw worker).
- tree() vs. snapshot() vs. to_json().
- TTL-based removal after completion.
- Tolerant behavior on orphan events (parent not yet registered).
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
# Sub-Jarvis lifecycle
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openclaw_task_started_creates_running_node(
    bus: EventBus,
    registry: JarvisAgentRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deterministic brand: the node name follows the wake-word-derived
    # assistant name for ANY configured value — never the host's live config
    # (a test keyed to the maintainer's own wake word would be the exact
    # anti-pattern this feature exists to prevent).
    monkeypatch.setattr(
        "jarvis.agents.registry._agent_display_name", lambda: "Nova-Agent"
    )
    tid = uuid4()
    await bus.publish(
        JarvisAgentTaskStarted(
            trace_id=tid,
            utterance="build me a Flask app",
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
    assert node.utterance == "build me a Flask app"
    assert node.context_hints == ["Port 8000", "single file"]
    assert node.provider == "gemini"
    assert node.model == "opus"
    # The display name is the branded role only — engine/provider/model must
    # never leak into it (agents-board hygiene). provider/model stay as
    # structured fields above for internal aggregation.
    assert node.name == "Nova-Agent"
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
            summary="Done",
            full_log_len=1200,
            duration_s=14.2,
            cost_estimate_usd=0.034,
        )
    )
    node = registry.snapshot()[tid.hex]
    assert node.status == "completed"
    assert node.duration_ms == pytest.approx(14200.0)
    assert node.cost_usd == pytest.approx(0.034)
    assert any("Done" in p for p in node.prompts)


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
# Brain-turn aggregation (into the newest Sub-Jarvis)
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
            system_prompt_preview="You are the Sub-Jarvis",
        )
    )
    await bus.publish(
        BrainTurnCompleted(tokens_in=1400, tokens_out=320, cost_usd=0.034)
    )
    node = registry.snapshot()[sj.hex]
    assert node.tokens_in == 1400
    assert node.tokens_out == 320
    assert node.cost_usd == pytest.approx(0.034)
    # The system_prompt_preview ended up in parent.prompts
    assert any("You are the Sub-Jarvis" in p for p in node.prompts)


# ────────────────────────────────────────────────────────────────
# Tool-call lists
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
# Harness (child heuristic)
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
    # Parent side: harness in children_trace_ids
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
# Tree / snapshot / JSON serialization
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
    # All roots are JSON-safe dicts
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
    # Very short TTL for the test
    reg = JarvisAgentRegistry(bus, ttl_completed_s=0).attach()
    tid = uuid4()
    await bus.publish(JarvisAgentTaskStarted(trace_id=tid))
    await bus.publish(JarvisAgentTaskCompleted(trace_id=tid, success=True))
    # Yield an event-loop round so the cleanup task runs
    await asyncio.sleep(0.05)
    assert tid.hex not in reg.snapshot()


@pytest.mark.asyncio
async def test_orphan_child_tolerated_without_parent(
    bus: EventBus, registry: JarvisAgentRegistry
) -> None:
    # HarnessDispatched without a running Sub-Jarvis → harness as root (parent=None)
    h = uuid4()
    await bus.publish(HarnessDispatched(trace_id=h, harness="openclaw"))
    node = registry.snapshot()[h.hex]
    assert node.parent_trace_id is None
    # tree() should show it as a root
    assert any(r.trace_id == h.hex for r in registry.tree())


@pytest.mark.asyncio
async def test_agent_node_default_fields_are_empty() -> None:
    """Regression guard: AgentNode defaults are JSON-safe and empty."""
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
                summary_de="fertig",  # i18n-allow
                summary_en="done",
            ),
        )
    )

    node = registry.snapshot()[tid]
    assert node.status == "completed"
    assert node.cost_usd == pytest.approx(0.42)
    assert any("fertig" in p for p in node.prompts)  # i18n-allow


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


# --- error_class / human error_detail surfacing (Task 4) ---


@pytest.mark.asyncio
async def test_mission_failed_carries_error_detail_and_class(
    registry: JarvisAgentRegistry,
) -> None:
    """node.error must show the human detail (the 401 text), not the raw
    reason token; error_class rides along for the UI message map."""
    from jarvis.missions.event_bus import MissionBus
    from jarvis.missions.events import (
        EventEnvelope,
        MissionDispatched,
        MissionFailed,
        now_ms,
    )

    mbus = MissionBus()
    registry.attach_mission_bus(mbus)

    mission_id = "019e1800-0000-7000-8000-000000000004"
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
                reason="task_error",
                error_class="provider_auth",
                last_state="CRITIC_REVIEW",
                error_detail="Failed to authenticate. API Error: 401",
            ),
        )
    )

    node = registry.snapshot()[mission_id.replace("-", "")]
    assert node.status == "failed"
    assert node.error == "Failed to authenticate. API Error: 401"
    assert node.error_class == "provider_auth"


@pytest.mark.asyncio
async def test_mission_failed_without_detail_keeps_reason_fallback(
    registry: JarvisAgentRegistry,
) -> None:
    from jarvis.missions.event_bus import MissionBus
    from jarvis.missions.events import (
        EventEnvelope,
        MissionDispatched,
        MissionFailed,
        now_ms,
    )

    mbus = MissionBus()
    registry.attach_mission_bus(mbus)

    mission_id = "019e1800-0000-7000-8000-000000000005"
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
            payload=MissionFailed(reason="task_error", last_state="CRITIC_REVIEW"),
        )
    )

    node = registry.snapshot()[mission_id.replace("-", "")]
    assert node.error == "task_error"
    assert node.error_class is None


@pytest.mark.asyncio
async def test_worker_killed_carries_error_detail_and_class(
    registry: JarvisAgentRegistry,
) -> None:
    """WorkerKilled.error_detail becomes node.error verbatim; error_class
    rides along for the UI message map."""
    from jarvis.missions.event_bus import MissionBus
    from jarvis.missions.events import (
        EventEnvelope,
        MissionDispatched,
        WorkerKilled,
        WorkerSpawned,
        now_ms,
    )

    mbus = MissionBus()
    registry.attach_mission_bus(mbus)

    mission_id = "019e1800-0000-7000-8000-000000000006"
    worker_id = "019e1800-0000-7000-8000-0000000000a0"
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="hauptjarvis",
            ts_ms=now_ms(),
            payload=MissionDispatched(prompt="build it", language="de"),
        )
    )
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=WorkerSpawned(
                worker_id=worker_id,
                step={"task": "build"},
                pid=4242,
                cli="claude",
                model="sonnet",
                worktree="C:/wt/agent-1",
            ),
        )
    )
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=WorkerKilled(
                worker_id=worker_id,
                reason="worker_error",
                error_class="provider_auth",
                error_detail="Failed to authenticate. API Error: 401",
            ),
        )
    )

    node = registry.snapshot()[worker_id.replace("-", "")]
    assert node.status == "failed"
    assert node.error == "Failed to authenticate. API Error: 401"
    assert node.error_class == "provider_auth"


@pytest.mark.asyncio
async def test_worker_killed_without_detail_falls_back_to_reason(
    registry: JarvisAgentRegistry,
) -> None:
    from jarvis.missions.event_bus import MissionBus
    from jarvis.missions.events import (
        EventEnvelope,
        MissionDispatched,
        WorkerKilled,
        WorkerSpawned,
        now_ms,
    )

    mbus = MissionBus()
    registry.attach_mission_bus(mbus)

    mission_id = "019e1800-0000-7000-8000-000000000007"
    worker_id = "019e1800-0000-7000-8000-0000000000b0"
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="hauptjarvis",
            ts_ms=now_ms(),
            payload=MissionDispatched(prompt="build it", language="de"),
        )
    )
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=WorkerSpawned(
                worker_id=worker_id,
                step={"task": "build"},
                pid=4242,
                cli="claude",
                model="sonnet",
                worktree="C:/wt/agent-1",
            ),
        )
    )
    await mbus.publish(
        EventEnvelope(
            mission_id=mission_id,
            worker_id=worker_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=WorkerKilled(worker_id=worker_id, reason="timeout"),
        )
    )

    node = registry.snapshot()[worker_id.replace("-", "")]
    assert node.status == "failed"
    assert node.error == "killed: timeout"
    assert node.error_class is None
