"""Per-agent routines = tagged tasks in the existing Automations scheduler.

No second scheduler (agent-definition §2, build plan wave 9): a routine is a
``TaskSpec`` with ``created_by="society"``, the tags ``society`` and
``agent:<agent_id>``, and a title prefixed ``[agent:<name>]``. It therefore
appears in the Automations section automatically (finish-it-everywhere)
and on the agent's model card through :func:`list_routines`. The task's
prompt carries the agent's identity the same way a dispatched mission
does, and its plugin grants are the unattended pre-authorization the task
runner already understands.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Final

from jarvis.tasks.schema import (
    AgentAction,
    PluginGrant,
    TaskSpec,
    TriggerAfterDelay,
    TriggerAtTime,
    TriggerEvery,
    TriggerOnEvent,
)

from .roster import AgentRecord

__all__ = [
    "ROUTINE_TAG",
    "agent_tag",
    "build_task_spec",
    "create_routine",
    "is_agent_routine",
    "list_routines",
]

ROUTINE_TAG: Final[str] = "society"
_MAX_ROUTINES: Final[int] = 50


def agent_tag(agent_id: str) -> str:
    return f"agent:{agent_id}"


def _trigger(schedule: dict[str, Any]) -> Any:
    kind = str(schedule.get("kind") or schedule.get("type") or "every")
    if kind == "every":
        return TriggerEvery(
            interval_seconds=float(schedule.get("interval_seconds") or 86_400),
            start_at=schedule.get("start_at"),
        )
    if kind == "at_time":
        return TriggerAtTime(iso_timestamp=str(schedule["iso_timestamp"]))
    if kind == "after_delay":
        return TriggerAfterDelay(delay_seconds=float(schedule["delay_seconds"]))
    if kind == "on_event":
        return TriggerOnEvent(
            event_name=str(schedule["event_name"]),
            filter_expr=schedule.get("filter_expr"),
            max_firings=schedule.get("max_firings"),
        )
    raise ValueError(f"unknown schedule kind {kind!r}")


def _routine_prompt(agent: AgentRecord, prompt: str) -> str:
    lines = [
        f"You are {agent.name}" + (f", {agent.title}" if agent.title else "") + ",",
        "an agent in the user's agent society led by Jarvis, running a scheduled routine.",
    ]
    if agent.description.strip():
        lines += ["", "Standing instructions:", agent.description.strip()]
    if agent.focus:
        lines += ["", "Reach for these capabilities first: " + ", ".join(agent.focus)]
    lines += ["", "Routine:", prompt.strip()]
    return "\n".join(lines)


def build_task_spec(
    agent: AgentRecord,
    *,
    title: str,
    prompt: str,
    schedule: dict[str, Any],
    plugin_grants: list[dict[str, str]] | None = None,
    announce_on_success: str | None = None,
) -> TaskSpec:
    grants = tuple(
        PluginGrant(plugin_id=str(g["plugin_id"]), scope=g.get("scope", "read"))  # type: ignore[arg-type]
        for g in (plugin_grants or [])
        if g.get("plugin_id")
    )
    clean_title = " ".join(title.split())[:200] or "routine"
    return TaskSpec(
        title=f"[agent:{agent.name}] {clean_title}",
        trigger=_trigger(schedule),
        action=AgentAction(prompt=_routine_prompt(agent, prompt), plugin_grants=grants),
        created_by="society",
        tags=(ROUTINE_TAG, agent_tag(agent.agent_id)),
        announce_on_success=announce_on_success,
    )


def agent_id_from_tags(tags: Sequence[str]) -> str | None:
    """The owning agent behind a task's tags (``agent:<id>``), else ``None``."""
    prefix = agent_tag("")
    for tag in tags:
        text = str(tag)
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return None


def _tags_of(row: dict[str, Any]) -> tuple[str, ...]:
    raw = row.get("spec_json")
    if not raw:
        return ()
    try:
        spec = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return ()
    tags = spec.get("tags") if isinstance(spec, dict) else None
    return tuple(str(t) for t in tags) if isinstance(tags, list | tuple) else ()


def is_agent_routine(row: dict[str, Any], agent_id: str) -> bool:
    return agent_tag(agent_id) in _tags_of(row)


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("spec_json")
    spec: dict[str, Any] = {}
    if raw:
        try:
            spec = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except ValueError:
            spec = {}
    return {
        "id": row.get("id"),
        "title": row.get("title") or spec.get("title"),
        "state": row.get("state"),
        "trigger": spec.get("trigger"),
        "due_at_ns": row.get("due_at_ns"),
        "last_run_ns": row.get("last_run_ns"),
        "tags": list(_tags_of(row)),
    }


async def list_routines(task_store: Any, agent_id: str) -> list[dict[str, Any]]:
    rows = await task_store.list(limit=1000)
    return [_summary(r) for r in rows if is_agent_routine(r, agent_id)]


async def create_routine(task_store: Any, scheduler: Any | None, spec: TaskSpec) -> str:
    """Schedule through the live scheduler when there is one, else insert."""
    if scheduler is not None:
        return str(await scheduler.schedule(spec))
    return str(await task_store.insert(spec))


async def count_routines(task_store: Any, agent_id: str) -> int:
    return len(await list_routines(task_store, agent_id))


MAX_ROUTINES_PER_AGENT: Final[int] = _MAX_ROUTINES


async def manage_routine(
    agent: AgentRecord, payload: dict[str, Any], task_store: Any, scheduler: Any
) -> str:
    """Modify an owned routine through the existing scheduler, preserving its id."""
    tid = str(payload["task_id"])
    row = await task_store.get(tid)
    if row is None or not is_agent_routine(row, agent.agent_id):
        raise ValueError("No routine with that id belongs to this agent")
    if scheduler is None:
        raise RuntimeError("The task scheduler is unavailable")
    operation = payload["operation"]
    if operation == "update":
        old = await task_store.get_spec(tid)
        spec = build_task_spec(
            agent,
            title=payload["title"],
            prompt=payload["prompt"],
            schedule=payload["schedule"],
            plugin_grants=[g.model_dump() for g in old.action.plugin_grants],
            announce_on_success=payload.get("announce_on_success"),
        )
        await scheduler.update_task(tid, spec.model_copy(update={"id": old.id}))
    elif operation == "pause":
        await scheduler.pause(tid)
    elif operation == "resume":
        await scheduler.resume(tid)
    elif operation == "delete":
        if row.get("state") == "running":
            raise ValueError("Pause or cancel the running routine before deleting it")
        await scheduler.cancel_task(tid)
        await task_store.delete(tid)
    else:
        raise ValueError("Unknown routine operation")
    return f"Routine {tid}: {operation} applied"
