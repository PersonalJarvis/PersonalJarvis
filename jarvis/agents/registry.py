"""JarvisAgentRegistry — live tree of all active Jarvis-Agents.

The registry is a typed bus subscriber: it holds an in-memory dict
of ``AgentNode`` instances and maintains parent-child links via the
``parent_trace_id`` field of the Phase-5.5 events.

Design decisions (see Plan §3):
- **Client-side tree building** is possible (all events reach the frontend
  via WebSocket anyway), but the registry provides a snapshot endpoint for
  the initial render (before WS events start flowing).
- **TTL 60s** for completed/failed nodes. An asyncio task sleeps, then
  removes the node from the map and from ``parent.children_trace_ids``.
- **Heuristic parent linking for HarnessDispatched**: the event has no
  ``parent_trace_id`` field. We take the most recently started still-running
  worker node as the parent. If none exists, the harness node becomes root.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    BrainTurnCompleted,
    BrainTurnStarted,
    HarnessCompleted,
    HarnessDispatched,
    JarvisAgentReviewTriggered,
    JarvisAgentTaskCompleted,
    JarvisAgentTaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from jarvis.missions.events import (
    CriticVerdictReady,
    EventEnvelope,
    MissionApproved,
    MissionCancelled,
    MissionDispatched,
    MissionFailed,
    MissionTimedOut,
    WorkerDraftReady,
    WorkerKilled,
    WorkerSpawned,
)

log = logging.getLogger(__name__)

NodeKind = Literal["router", "jarvis_agent", "harness", "tool_call"]
NodeStatus = Literal["running", "completed", "failed"]


def _tid(uuid_or_str: UUID | str | None) -> str | None:
    if uuid_or_str is None:
        return None
    if isinstance(uuid_or_str, UUID):
        return uuid_or_str.hex
    return str(uuid_or_str).replace("-", "")


def _agent_display_name() -> str:
    """Public display name for an agent node — the wake-word-derived brand.

    "Ruben" -> "Ruben-Agent", for ANY configured wake word (2026-07-17
    rebrand); resolution failures fall back to the neutral "Assistant-Agent".
    Read per node creation so a wake-word change applies without a restart.
    """
    try:
        from jarvis.brain.assistant_name import agent_brand
        from jarvis.core.config import load_config

        return agent_brand(load_config())
    except Exception:  # noqa: BLE001 — a config hiccup must not drop the node
        from jarvis.brain.assistant_name import agent_brand_from_name

        return agent_brand_from_name("")


@dataclass
class AgentNode:
    """A node in the sub-agent tree (delivered 1:1 as JSON to the UI)."""

    trace_id: str
    kind: NodeKind
    name: str
    status: NodeStatus = "running"
    parent_trace_id: str | None = None
    provider: str | None = None
    model: str | None = None
    started_ns: int = 0
    completed_ns: int | None = None
    duration_ms: float | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    utterance: str | None = None
    context_hints: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    children_trace_ids: list[str] = field(default_factory=list)
    error: str | None = None
    error_class: str | None = None
    review_iterations: int = 0
    depth: int = 0


class JarvisAgentRegistry:
    """Live tree registry for the Jarvis-Agent dashboard.

    Usage::

        registry = JarvisAgentRegistry(bus)
        registry.attach()              # activate bus subscriptions
        roots = registry.tree()        # root nodes only (no parent)
        snap  = registry.snapshot()    # flat Dict[trace_id, AgentNode]
    """

    def __init__(self, bus: EventBus, *, ttl_completed_s: int = 60) -> None:
        self._bus = bus
        self._ttl = ttl_completed_s
        self._nodes: dict[str, AgentNode] = {}
        self._attached = False
        self._mission_bus_attached = False
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    def attach(self) -> JarvisAgentRegistry:
        if self._attached:
            return self
        self._bus.subscribe(JarvisAgentTaskStarted, self._on_worker_started)
        self._bus.subscribe(JarvisAgentReviewTriggered, self._on_worker_review)
        self._bus.subscribe(JarvisAgentTaskCompleted, self._on_worker_completed)
        self._bus.subscribe(BrainTurnStarted, self._on_brain_turn_started)
        self._bus.subscribe(BrainTurnCompleted, self._on_brain_turn_completed)
        self._bus.subscribe(ToolCallStarted, self._on_tool_call_started)
        self._bus.subscribe(ToolCallCompleted, self._on_tool_call_completed)
        self._bus.subscribe(HarnessDispatched, self._on_harness_dispatched)
        self._bus.subscribe(HarnessCompleted, self._on_harness_completed)
        self._attached = True
        return self

    def attach_mission_bus(self, mission_bus: Any) -> JarvisAgentRegistry:
        """Bridge Phase-6 MissionBus events into the legacy agent-tree.

        Welle-4 removed the publishers for OpenClawTaskStarted/Completed et
        al. — the new Mission-Manager emits Phase-6 EventEnvelopes on its own
        per-subscriber bus. Without this bridge the Sub-Agents board stays
        empty even while missions are running. We translate the few relevant
        envelopes into AgentNode upserts so the existing REST + WS surfaces
        light up unchanged.
        """
        if self._mission_bus_attached:
            return self
        mission_bus.subscribe_all(self._on_mission_envelope)
        self._mission_bus_attached = True
        return self

    def clear(self) -> None:
        for t in self._cleanup_tasks:
            t.cancel()
        self._cleanup_tasks.clear()
        self._nodes.clear()

    def snapshot(self) -> dict[str, AgentNode]:
        return dict(self._nodes)

    def tree(self) -> list[AgentNode]:
        return [
            n for n in self._nodes.values()
            if n.parent_trace_id is None or n.parent_trace_id not in self._nodes
        ]

    def _attach_child(self, parent_tid: str | None, child_tid: str) -> None:
        if parent_tid is None:
            return
        parent = self._nodes.get(parent_tid)
        if parent is None:
            return
        if child_tid not in parent.children_trace_ids:
            parent.children_trace_ids.append(child_tid)

    def _find_newest_running_worker(self) -> AgentNode | None:
        candidates = [
            n for n in self._nodes.values()
            if n.kind == "jarvis_agent" and n.status == "running"
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda n: n.started_ns)

    async def _on_worker_started(self, e: JarvisAgentTaskStarted) -> None:
        tid = _tid(e.trace_id)
        if tid is None:
            return
        parent_tid = _tid(e.parent_trace_id)
        # User-facing name is the role only, branded with the wake-word-derived
        # assistant name. provider/model are kept as structured fields (below)
        # for internal aggregation, but never baked into the display name — the
        # agent board must not surface the underlying engine or model. `kind`
        # stays the internal routing tag.
        name = _agent_display_name()
        self._nodes[tid] = AgentNode(
            trace_id=tid, kind="jarvis_agent", name=name, status="running",
            parent_trace_id=parent_tid,
            provider=e.provider or None, model=e.model or None,
            started_ns=e.timestamp_ns,
            utterance=e.utterance,
            context_hints=list(e.context_hints),
            depth=e.depth,
        )
        self._attach_child(parent_tid, tid)

    async def _on_worker_review(self, e: JarvisAgentReviewTriggered) -> None:
        tid = _tid(e.trace_id)
        if tid is None or tid not in self._nodes:
            return
        self._nodes[tid].review_iterations = max(
            self._nodes[tid].review_iterations, e.iteration
        )

    async def _on_worker_completed(self, e: JarvisAgentTaskCompleted) -> None:
        tid = _tid(e.trace_id)
        if tid is None or tid not in self._nodes:
            return
        node = self._nodes[tid]
        node.status = "completed" if e.success else "failed"
        node.completed_ns = e.timestamp_ns
        node.duration_ms = e.duration_s * 1000.0
        node.cost_usd = e.cost_estimate_usd
        node.error = e.error
        if e.summary:
            node.prompts.append(f"[summary] {e.summary}")
        self._schedule_removal(tid)

    async def _on_brain_turn_started(self, e: BrainTurnStarted) -> None:
        parent_tid = _tid(e.parent_trace_id)
        if parent_tid is None or parent_tid not in self._nodes:
            return
        parent = self._nodes[parent_tid]
        if e.system_prompt_preview:
            parent.prompts.append(e.system_prompt_preview)
        if not parent.provider and e.provider:
            parent.provider = e.provider
        if not parent.model and e.model:
            parent.model = e.model

    async def _on_brain_turn_completed(self, e: BrainTurnCompleted) -> None:
        running = [
            n for n in self._nodes.values()
            if n.kind == "jarvis_agent" and n.status == "running"
        ]
        if not running:
            return
        newest = max(running, key=lambda n: n.started_ns)
        newest.tokens_in += e.tokens_in
        newest.tokens_out += e.tokens_out
        newest.cost_usd += e.cost_usd

    async def _on_tool_call_started(self, e: ToolCallStarted) -> None:
        parent_tid = _tid(e.parent_trace_id)
        if parent_tid is None or parent_tid not in self._nodes:
            return
        self._nodes[parent_tid].tool_calls.append({
            "trace_id": _tid(e.trace_id),
            "tool_name": e.tool_name,
            "args_preview": e.args_preview,
            "started_ns": e.timestamp_ns,
            "status": "running",
        })

    async def _on_tool_call_completed(self, e: ToolCallCompleted) -> None:
        tc_tid = _tid(e.trace_id)
        for parent in self._nodes.values():
            for entry in parent.tool_calls:
                if entry.get("trace_id") == tc_tid and entry.get("status") == "running":
                    entry["status"] = "completed" if e.success else "failed"
                    entry["duration_ms"] = e.duration_ms
                    entry["output_preview"] = e.output_preview
                    entry["error"] = e.error
                    return

    async def _on_harness_dispatched(self, e: HarnessDispatched) -> None:
        tid = _tid(e.trace_id)
        if tid is None:
            return
        parent = self._find_newest_running_worker()
        parent_tid = parent.trace_id if parent else None
        self._nodes[tid] = AgentNode(
            trace_id=tid, kind="harness",
            name="Worker",
            status="running",
            parent_trace_id=parent_tid,
            started_ns=e.timestamp_ns,
        )
        self._attach_child(parent_tid, tid)

    async def _on_harness_completed(self, e: HarnessCompleted) -> None:
        tid = _tid(e.trace_id)
        if tid is None or tid not in self._nodes:
            return
        node = self._nodes[tid]
        success = False
        duration_ms: float | None = None
        if e.result is not None:
            success = getattr(e.result, "exit_code", 1) == 0
            dur_ms = getattr(e.result, "duration_ms", None)
            if dur_ms is not None:
                duration_ms = float(dur_ms)
        node.status = "completed" if success else "failed"
        node.completed_ns = e.timestamp_ns
        node.duration_ms = duration_ms
        self._schedule_removal(tid)

    def _schedule_removal(self, tid: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._remove_after_ttl(tid))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    def _mark_running_children_terminal(
        self, parent_tid: str, ts_ns: int, *, success: bool
    ) -> None:
        """When a mission terminates, sweep its still-`running` children.

        Without this hook the Worker rows shown in the Sub-Agents board stay
        ACTIVE indefinitely — the per-worker WorkerDraftReady/WorkerKilled
        events only cover normal exits, not the case where the mission ends
        first (e.g. critic-exhausted or hard timeout cascading down).
        """
        parent = self._nodes.get(parent_tid)
        if parent is None:
            return
        for child_tid in list(parent.children_trace_ids):
            child = self._nodes.get(child_tid)
            if child is None or child.status != "running":
                continue
            child.status = "completed" if success else "failed"
            child.completed_ns = ts_ns
            if not success and not child.error:
                child.error = "mission ended before worker exit"

    async def _remove_after_ttl(self, tid: str) -> None:
        try:
            await asyncio.sleep(self._ttl)
        except asyncio.CancelledError:
            return
        node = self._nodes.pop(tid, None)
        if node is None or node.parent_trace_id is None:
            return
        parent = self._nodes.get(node.parent_trace_id)
        if parent is not None and tid in parent.children_trace_ids:
            parent.children_trace_ids.remove(tid)

    async def _on_mission_envelope(self, envelope: EventEnvelope) -> None:
        """Wildcard handler — translate a single MissionBus envelope.

        Mission-IDs are uuid7 strings; we keep them as-is (no dash-stripping
        beyond `_tid`) so the trace_id matches both REST `/api/sub-agents` and
        the mission-id the user already sees in the missions REST + DB. Errors
        in this handler are swallowed by the MissionBus wrapper (same pattern
        as `_safe_dispatch` in `jarvis.core.bus.EventBus`) — a buggy mapper
        must never block mission flow.
        """
        payload = envelope.payload
        mission_id = envelope.mission_id
        tid = _tid(mission_id)
        if tid is None:
            return
        ts_ns = int(envelope.ts_ms) * 1_000_000

        if isinstance(payload, MissionDispatched):
            if tid in self._nodes:
                # Re-dispatch of the same mission_id is unusual — keep the
                # original node; only refresh started_ns if it was zero.
                node = self._nodes[tid]
                if node.started_ns == 0:
                    node.started_ns = ts_ns
                return
            self._nodes[tid] = AgentNode(
                trace_id=tid,
                kind="jarvis_agent",
                # Role label branded with the wake-word-derived assistant name —
                # no engine/provider/model/language in the name. The prompt
                # (what it is doing) rides `utterance`.
                name=_agent_display_name(),
                status="running",
                started_ns=ts_ns,
                utterance=payload.prompt,
                # parent_mission_id is a uuid7 too — fits the same trace_id
                # space, so the existing parent/child linking works.
                parent_trace_id=_tid(payload.parent_mission_id),
            )
            self._attach_child(_tid(payload.parent_mission_id), tid)
            return

        if isinstance(payload, WorkerSpawned):
            # Reuse the harness-node shape (kind="harness") so the UI does not
            # need a new icon/type. worker_id (uuid7) is the trace_id.
            worker_tid = _tid(payload.worker_id) or payload.worker_id
            self._nodes[worker_tid] = AgentNode(
                trace_id=worker_tid,
                # Neutral role label — the worker's cli/model stay as structured
                # fields (provider/model) for internal use but are not shown.
                kind="harness",
                name="Worker",
                status="running",
                parent_trace_id=tid,
                started_ns=ts_ns,
                provider=payload.cli,
                model=payload.model,
            )
            self._attach_child(tid, worker_tid)
            return

        if isinstance(payload, WorkerDraftReady):
            # The worker shipped a draft; the mission may still loop through
            # more iterations, but THIS worker subprocess is done.
            worker_tid = _tid(payload.worker_id) or payload.worker_id
            node = self._nodes.get(worker_tid)
            if node is None:
                return
            node.status = "completed"
            node.completed_ns = ts_ns
            node.tokens_out = payload.tokens_used
            node.cost_usd = payload.cost_usd
            return

        if isinstance(payload, WorkerKilled):
            worker_tid = _tid(payload.worker_id) or payload.worker_id
            node = self._nodes.get(worker_tid)
            if node is None:
                return
            node.status = "failed"
            node.completed_ns = ts_ns
            node.error = payload.error_detail or f"killed: {payload.reason}"
            node.error_class = payload.error_class
            return

        if isinstance(payload, CriticVerdictReady):
            node = self._nodes.get(tid)
            if node is None:
                return
            node.review_iterations = max(
                node.review_iterations, payload.iteration + 1
            )
            return

        if isinstance(payload, MissionApproved):
            node = self._nodes.get(tid)
            if node is None:
                return
            node.status = "completed"
            node.completed_ns = ts_ns
            node.duration_ms = float(payload.wall_ms)
            node.cost_usd = payload.cost_usd
            node.tokens_out = payload.tokens_used
            if payload.summary_de:
                node.prompts.append(f"[summary] {payload.summary_de}")
            self._mark_running_children_terminal(tid, ts_ns, success=True)
            self._schedule_removal(tid)
            return

        if isinstance(payload, (MissionFailed, MissionCancelled, MissionTimedOut)):
            node = self._nodes.get(tid)
            if node is None:
                return
            node.status = "failed"
            node.completed_ns = ts_ns
            if isinstance(payload, MissionFailed):
                node.error = payload.error_detail or payload.reason
                node.error_class = payload.error_class
            elif isinstance(payload, MissionCancelled):
                node.error = f"cancelled: {payload.reason}"
            else:
                node.error = "timed out"
            self._mark_running_children_terminal(tid, ts_ns, success=False)
            self._schedule_removal(tid)
            return

    def to_json(self) -> dict[str, Any]:
        return {
            "roots": [dataclasses.asdict(n) for n in self.tree()],
            "all": {tid: dataclasses.asdict(n) for tid, n in self._nodes.items()},
            "count": len(self._nodes),
            "server_ts_ns": time.time_ns(),
        }
