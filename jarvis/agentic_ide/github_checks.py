"""Every GitHub check state, aggregated over all checks rather than one page."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from jarvis.agentic_ide.git_overview import CiStatus

CHECK_STATES = {
    "ACTION_REQUIRED": "action_required",
    "CANCELLED": "cancelled",
    "FAILURE": "failure",
    "NEUTRAL": "neutral",
    "SKIPPED": "skipped",
    "STALE": "stale",
    "STARTUP_FAILURE": "startup_failure",
    "SUCCESS": "success",
    "TIMED_OUT": "timed_out",
    "COMPLETED": "unknown",
    "IN_PROGRESS": "running",
    "PENDING": "pending",
    "QUEUED": "pending",
    "REQUESTED": "requested",
    "WAITING": "waiting",
}
CONTEXT_STATES = {
    "ERROR": "error",
    "EXPECTED": "expected",
    "FAILURE": "failure",
    "PENDING": "pending",
    "SUCCESS": "success",
}
PRIORITY = (
    "action_required",
    "startup_failure",
    "timed_out",
    "failure",
    "error",
    "running",
    "waiting",
    "requested",
    "pending",
    "expected",
    "unknown",
    "cancelled",
    "stale",
    "success",
    "neutral",
    "skipped",
)


@dataclass(slots=True)
class CheckStatus(CiStatus):
    states: list[str] = field(default_factory=list)


def _node_state(node: dict[str, Any]) -> str:
    if node.get("__typename") == "StatusContext":
        return CONTEXT_STATES.get(node.get("state"), "unknown")
    raw = node.get("conclusion") if node.get("status") == "COMPLETED" else node.get("status")
    return CHECK_STATES.get(raw, "unknown")


def checks(rollup: dict[str, Any] | None, commit: str) -> CheckStatus:
    ci = CheckStatus(commit=commit[:12])
    if not rollup:
        return ci
    contexts = rollup.get("contexts") or {}
    nodes = [node for node in contexts.get("nodes", []) if isinstance(node, dict)]
    counts: Counter[str] = Counter()
    complete = "checkRunCountsByState" in contexts and "statusContextCountsByState" in contexts
    if complete:
        for key, vocabulary in (
            ("checkRunCountsByState", CHECK_STATES),
            ("statusContextCountsByState", CONTEXT_STATES),
        ):
            for item in contexts.get(key) or []:
                counts[vocabulary.get(item.get("state"), "unknown")] += int(item.get("count") or 0)
    else:
        for node in nodes:
            counts[_node_state(node)] += 1
        complete = contexts.get("totalCount", len(nodes)) == len(nodes)
    if not complete:
        # Never paint green from the first page of a partial/older API answer.
        aggregate = CONTEXT_STATES.get(rollup.get("state"), "unknown")
        counts[aggregate] += max(1, int(contexts.get("totalCount") or 0) - len(nodes))
    ci.states = [state for state in PRIORITY if counts[state]]
    ci.state = (
        ci.states[0] if ci.states else ("none" if not contexts.get("totalCount") else "unknown")
    )
    ci.total = int(contexts.get("totalCount") or sum(counts.values()))
    ci.passed = sum(counts[state] for state in ("success", "neutral", "skipped"))
    ci.failed = sum(
        counts[state]
        for state in ("failure", "error", "startup_failure", "timed_out", "action_required")
    )
    ci.running = counts["running"]
    ci.pending = sum(counts[state] for state in ("pending", "expected", "requested", "waiting"))
    matching = [node for node in nodes if _node_state(node) == ci.state]
    ci.names = [str(node.get("name") or node.get("context") or "") for node in matching[:6]]
    if matching:
        node = matching[0]
        workflow = ((node.get("checkSuite") or {}).get("workflowRun") or {}).get("url")
        ci.url = str(workflow or node.get("detailsUrl") or node.get("targetUrl") or "")
    return ci
