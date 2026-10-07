"""Track only Jarvis-requested pane jobs and return fresh, attributed evidence."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from loguru import logger

from jarvis.core.delegation import result_announcement

from . import agent_transcript


@dataclass(frozen=True, slots=True)
class PendingResult:
    request_id: str
    prompt: str
    request: str
    language: str
    baseline: str
    generation: int
    reply_session_id: str = ""
    submitted_at: float | None = None
    prepared_at: float = 0.0


def _events(term: Any) -> list:
    from .session import account_home

    # A remote pane's local transcript belongs to its old process.
    if getattr(term, "computer_id", "") or term.resume is None:
        return []
    if not agent_transcript.can_read(term.agent):
        return []
    events = agent_transcript.read_events(
        term.agent, term.resume.id, home=account_home(term.agent, term.account),
        live=True,
    ) or []
    # Tool output is unnecessary for correlation and can be very large. Keep
    # work boundaries, so a new turn still invalidates an earlier completion.
    return [event for event in events if event["kind"] in {
        "user_message", "assistant_text", "turn_started", "turn_finished",
    }]


def _fingerprint(events: list) -> str:
    return hashlib.sha256(repr(events).encode()).hexdigest()


def _snapshot(term: Any) -> list:
    try:
        return _events(term)
    except Exception:  # A missing transcript cannot prevent authorized delivery.
        logger.opt(exception=True).debug("Delegated pane transcript unavailable")
        return []


def _completion_report(events: list, pending: PendingResult) -> str:
    """Read the final answer to this submission, preserving message boundaries.

    Display turns merge adjacent user messages (including repository instructions)
    and assistant progress. They cannot identify a work order or its final report.
    The live event reader retains those boundaries and requires a CLI end marker.
    """
    if not events or _fingerprint(events) == pending.baseline:
        return ""
    start = next(
        (i for i in range(len(events) - 1, -1, -1) if events[i]["kind"] == "user_message"),
        None,
    )
    if start is None:
        return ""
    user = events[start]
    expected = agent_transcript._clip(agent_transcript._spoken(pending.prompt))
    if (
        int(user.get("ts_ms") or 0)
        < int((pending.prepared_at or pending.submitted_at or 0) * 1000)
        or " ".join(user["payload"].get("text", "").split()) != " ".join(expected.split())
    ):
        return ""
    own = events[start + 1:]
    if not own or own[-1]["kind"] != "turn_finished":
        return ""
    finish = own[-1]["payload"]
    if finish.get("status") != "done" or not finish.get("turn_id"):
        return ""
    return next(
        (
            str(event["payload"].get("text") or "").strip()
            for event in reversed(own)
            if event["kind"] == "assistant_text"
            and event["payload"].get("turn_id") == finish["turn_id"]
        ),
        "",
    )


async def prepare(term: Any, prompt: str, request: str, origin: dict[str, str]) -> PendingResult:
    # The CLI can record the prompt before its input receipt is confirmed and
    # last_submit_at is stamped. Freshness starts before that write, not after.
    prepared_at = time.time()
    events = await asyncio.to_thread(_snapshot, term)
    return PendingResult(
        request_id=uuid4().hex, prompt=prompt, request=request or prompt,
        language=origin.get("lang", ""), baseline=_fingerprint(events),
        generation=term.process_generation,
        reply_session_id=origin.get("reply_session_id", ""),
        prepared_at=prepared_at,
    )


def submitted(term: Any, pending: PendingResult | None) -> None:
    # An uncertain receipt is tracked, but never called completed on its own.
    term.delegation_result = (
        replace(pending, prompt=term.last_prompt, submitted_at=term.last_submit_at)
        if pending is not None and term.submitted is not False else None
    )
    term.delegation_probe_at = 0.0


def current(term: Any, pending: PendingResult) -> bool:
    return (
        getattr(term, "delegation_result", None) is pending
        and term.process_generation == pending.generation
        and term.last_submit_at == pending.submitted_at
    )


async def publish_result(
    kind: str, term: Any, pending: PendingResult, publish: Any,
) -> bool:
    """Return an observed stop/question, never convert silence into success."""
    if publish is None or not current(term, pending):
        return False
    from .activity import shows_question
    from .task_state import probe

    proof = await probe(term)
    expected = {"completed": "completed", "stopped": "stopped"}
    if kind in {"completed", "stopped"} and (proof is None or proof.state != expected[kind]):
        return False
    if kind == "needs_input" and proof.state != "asking" and not shows_question(term):
        return False
    try:
        events = await asyncio.to_thread(_snapshot, term)
        if not current(term, pending):
            return False
        proof = await probe(term)
        if not current(term, pending):
            return False
        if kind in {"completed", "stopped"} and (proof is None or proof.state != expected[kind]):
            return False
        if kind == "needs_input" and proof.state != "asking" and not shows_question(term):
            return False
        report = ""
        evidence = "terminal_state_only; task success is unverified"
        if kind == "completed":
            report = _completion_report(events, pending)
            # Completion and transcript persistence can be observed separately.
            # Keep the request pending for the next sweep instead of delivering
            # an empty report that the conversation mistakes for missing work.
            if not report:
                return False
            evidence = (
                "recorded CLI completion and matching final report; not independent verification"
            )
        elif kind == "needs_input":
            report = "\n".join(term.transcript.tail(20))[-3000:]
            evidence = "current terminal question; not a completed task"
        elif kind == "stopped":
            report = "The CLI recorded an interrupted task; no completion is claimed."
            evidence = "explicit interruption in the current CLI session"
        elif kind in {"failed", "exited"}:
            report = f"Process state: {kind}; exit code: {getattr(term, 'exit_code', None)}"
        event = result_announcement(
            source="agentic_ide.readback", request_id=pending.request_id,
            name=str(term.name), request=pending.request, status=kind,
            report=report, language=pending.language, evidence=evidence,
        )
        if pending.reply_session_id:
            from jarvis.core.events import DelegationResultReady

            event = DelegationResultReady(
                trace_id=event.trace_id, source_layer="agentic_ide.followthrough",
                session_id=pending.reply_session_id, request_id=pending.request_id,
                agent_name=str(term.name), status=kind, text=event.text, report=event.report or "",
            )
        value = publish(event)
        if inspect.isawaitable(value):
            await value
        if kind != "needs_input" and current(term, pending):
            term.delegation_result = None
        return True
    except Exception:
        logger.opt(exception=True).warning("Delegated pane result delivery failed")
        return False


async def poll_ready(registry: Any, publish: Any, *, now: float | None = None) -> None:
    """Recover short jobs that finished entirely between two activity sweeps.

    Without an observed working-to-idle transition, only a fresh, correlated
    recorded answer is sufficient. Startup paint and old transcripts stay quiet.
    """
    from .notifications import SETTLE_S, STILL_S

    if publish is None:
        return
    moment = time.time() if now is None else now
    for session in registry.sessions:
        for term in session.terminals:
            pending = getattr(term, "delegation_result", None)
            if pending is None or not current(term, pending):
                continue
            last_activity = max(pending.submitted_at or moment, term.last_output_at or 0)
            if moment - last_activity < STILL_S + SETTLE_S:
                continue
            if moment - getattr(term, "delegation_probe_at", 0.0) < 10.0:
                continue
            kind = {
                "waiting": "completed", "stopped": "stopped",
                "failed": "failed", "exited": "exited",
            }.get(term.reading().activity)
            if kind is not None:
                term.delegation_probe_at = moment
                await publish_result(kind, term, pending, publish)
