"""Persist goal-derived background work through the existing task scheduler.

The model chooses useful follow-ups. This boundary requires trusted current
user provenance, preserves cancellation tombstones, and reads back the stored
task before acknowledging it. No scheduler or model call is created here.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from jarvis.core.protocols import ChatTurn, ToolResult
from jarvis.tasks.schema import RetryPolicy

from .proposals import ProposalRefused, validate
from .routines import (
    MAX_ROUTINES_PER_AGENT,
    build_task_spec,
    count_routines,
    manage_routine,
    next_run_readback,
)

AUTONOMOUS_TAG = "autonomous"
ORIGIN_SESSION_TAG = "origin-session:"
ORIGIN_PERMISSION_TAG = "origin-permission:"
log = logging.getLogger(__name__)

_NO_BACKGROUND = re.compile(
    r"\b(?:do not|don't|never)\s+(?:schedule|automate|create\s+(?:a\s+)?routine)|"
    r"\b(?:no\s+(?:background|automation)|nur\s+jetzt|"  # i18n-allow
    r"keine?\s+(?:automatisierung|hintergrundarbeit)|"  # i18n-allow
    r"nicht\s+(?:automatisieren|planen)|no\s+automatices)\b",  # i18n-allow
    re.I,
)
_ONCE = re.compile(
    r"\b(?:only\s+(?:once|this\s+time)|just\s+(?:once|today)|one.off|"
    r"nur\s+(?:einmal|diesmal|heute)|einmalig|solo\s+una\s+vez|"  # i18n-allow
    r"no\s+routine|keine?\s+routine)\b",  # i18n-allow
    re.I,
)
_INFORMATION = re.compile(
    r"^\s*(?:(?:please|bitte)\s+)?(?:explain|translate|quote|imagine|suppose|"  # i18n-allow
    r"what\s+(?:is|are|would)|how\s+(?:do|does|can|would)|"
    r"if\s+(?:i|we|you)\s+(?:wanted|were|could|would)|"
    r"erkl[aä]r\w*|[uü]bersetz\w*|zitiere|was\s+(?:ist|sind|w[aä]re)|"  # i18n-allow
    r"wie\s+(?:kann|geht|funktioniert)|"  # i18n-allow
    r"wenn\s+ich\b[^.!?]{0,80}\b(?:würde|wollte|könnte)|explica|traduce)\b",  # i18n-allow
    re.I,
)


def is_informational_request(text: str) -> bool:
    text = text.strip()
    return (
        not text
        or text.startswith(('"', "'", "“", "„", ">", "```"))
        or bool(_INFORMATION.search(text))
    )


def request_refusal(text: str, *, recurring: bool) -> str | None:
    """Backstop explicit exclusions; semantic task selection stays with the agent."""
    text = text.strip()
    if is_informational_request(text):
        return "Information, quoted text and hypothetical requests do not authorize background work"
    if _NO_BACKGROUND.search(text):
        return "The current request excludes background work"
    if recurring and _ONCE.search(text):
        return "The current request is one-time work; use a one-time trigger"
    return None


async def readback(store: Any, task_id: str, *, reused: bool = False) -> ToolResult:
    row = await store.get(task_id)
    spec = await store.get_spec(task_id)
    if row is None or spec is None:
        return ToolResult(False, {}, "Scheduled task could not be read back")
    trigger = spec.trigger.model_dump(mode="json")
    return ToolResult(
        True,
        {
            "applied": True,
            "kind": "routine",
            "task_id": task_id,
            "state": row["state"],
            "reused": reused,
            "trigger": trigger,
            "next_run": next_run_readback(row.get("due_at_ns"), trigger.get("timezone")),
            "note": "Report this stored state; a paused or terminal task has not been restarted.",
        },
    )


async def apply_autonomous_routine(
    runtime: Any,
    agent: Any,
    turn: ChatTurn,
    args: dict[str, Any],
) -> ToolResult:
    """Register a justified follow-up without a redundant configuration card."""
    if args.get("kind") != "routine":
        return ToolResult(False, {}, "Autonomous mode is only for tasks and routines")
    reason = str(args.get("reason") or "").strip()[:600]
    if not reason:
        return ToolResult(False, {}, "Explain how this work advances the current user goal")
    if len(str(args.get("request_quote") or "")) > 4000:
        return ToolResult(False, {}, "Quote the relevant goal in at most 4000 characters")
    raw = args.get("payload") or {}
    if isinstance(raw, dict) and raw.get("operation", "create") in {"create", "update"}:
        schedule = raw.get("schedule") or {}
        if not isinstance(schedule, dict) or not (schedule.get("kind") or schedule.get("type")):
            return ToolResult(False, {}, "Choose an explicit one-time or recurring trigger")
        if (schedule.get("kind") or schedule.get("type")) == "every" and not schedule.get(
            "interval_seconds"
        ):
            return ToolResult(False, {}, "An elapsed recurring schedule needs interval_seconds")
        if raw.get("workflow_id"):
            return ToolResult(
                False, {}, "Native workflow configuration requires explicit apply mode"
            )
    try:
        payload = validate("routine", args.get("payload"), catalog=runtime.catalog())
    except (ProposalRefused, ValueError) as exc:
        log.info("Autonomous routine validation refused (%s)", type(exc).__name__)
        return ToolResult(False, {}, str(exc))
    store, scheduler = runtime.task_services()
    if store is None or scheduler is None:
        return ToolResult(False, {}, "The task scheduler is unavailable; no work was registered")
    operation = payload.get("operation", "create")
    if operation != "create":
        if is_informational_request(turn.user_text):
            return ToolResult(False, {}, "An information question does not modify existing work")
        if operation == "update":
            refusal = request_refusal(
                turn.user_text,
                recurring=payload["schedule"]["kind"] not in {"at_time", "after_delay"},
            )
            if refusal:
                return ToolResult(False, {}, refusal)
        # Corrections and cancellations apply to existing owned work, even when
        # the new message says not to schedule anything else.
        try:
            await manage_routine(agent, payload, store, scheduler)
        except (ValueError, KeyError, RuntimeError) as exc:
            log.info("Autonomous routine update refused (%s)", type(exc).__name__)
            return ToolResult(False, {}, str(exc))
        if operation == "delete" and await store.get(payload["task_id"]) is None:
            return ToolResult(
                True, {"applied": True, "task_id": payload["task_id"], "state": "deleted"}
            )
        return await readback(store, payload["task_id"])
    schedule = payload["schedule"]
    recurring = schedule["kind"] not in {"at_time", "after_delay"}
    refusal = request_refusal(turn.user_text, recurring=recurring)
    if refusal:
        return ToolResult(False, {}, refusal)
    try:
        spec = build_task_spec(
            agent,
            title=payload["title"],
            prompt=payload["prompt"],
            schedule=schedule,
        )
    except (ValueError, KeyError) as exc:
        log.info("Autonomous routine trigger refused (%s)", type(exc).__name__)
        return ToolResult(False, {}, str(exc))
    # A stable semantic key survives new turn ids, reconnects and process
    # restarts. Never resurrect completed/cancelled work on duplicate delivery.
    key = json.dumps(
        {
            "agent": agent.agent_id,
            "goal": " ".join(str(args["request_quote"]).split()).casefold(),
            "work": " ".join(payload["prompt"].split()).casefold(),
            "schedule": spec.trigger.model_dump(mode="json"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    task_id = str(uuid5(NAMESPACE_URL, "jarvis:autonomous:" + key))
    if await store.get(task_id) is not None:
        return await readback(store, task_id, reused=True)
    if await count_routines(store, agent.agent_id) >= MAX_ROUTINES_PER_AGENT:
        return ToolResult(False, {}, "Routine cap reached")
    # Provenance is part of the same durable task insert, not a second write
    # which could be lost after scheduling. Existing task tags carry routing.
    origin = json.dumps(
        {
            "session_id": turn.session_id,
            "turn_id": turn.turn_id,
            "request_quote": args["request_quote"],
            "reason": reason,
        },
        ensure_ascii=False,
    )
    session = runtime.chat_service().store.get_session(turn.session_id)
    if session is None:
        return ToolResult(False, {}, "The originating chat is unavailable")
    spec = spec.model_copy(
        update={
            "id": uuid5(NAMESPACE_URL, "jarvis:autonomous:" + key),
            "tags": (
                *spec.tags,
                AUTONOMOUS_TAG,
                ORIGIN_SESSION_TAG + turn.session_id,
                ORIGIN_PERMISSION_TAG + session.permission_mode,
                "origin:" + origin,
            ),
            "retry_policy": RetryPolicy(retry_on_interrupt=False),
        }
    )
    try:
        await scheduler.schedule(spec, trace_id=turn.trace_id)
    except sqlite3.IntegrityError:
        # Concurrent identical submissions race on the task's primary key.
        if await store.get(task_id) is None:
            raise
        return await readback(store, task_id, reused=True)
    return await readback(store, task_id)
