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


def _turns(term: Any) -> list:
    from . import agent_transcript
    from .session import account_home

    # A remote pane's local transcript belongs to its old process.
    if getattr(term, "computer_id", "") or term.resume is None:
        return []
    if not agent_transcript.can_read(term.agent):
        return []
    return agent_transcript.read(
        term.agent, term.resume.id, home=account_home(term.agent, term.account),
    ) or []


def _fingerprint(turns: list) -> str:
    return hashlib.sha256(repr([(t.role, t.text) for t in turns]).encode()).hexdigest()


def _snapshot(term: Any) -> list:
    try:
        return _turns(term)
    except Exception:  # A missing transcript cannot prevent authorized delivery.
        logger.opt(exception=True).debug("Delegated pane transcript unavailable")
        return []


async def prepare(term: Any, prompt: str, request: str, origin: dict[str, str]) -> PendingResult:
    turns = await asyncio.to_thread(_snapshot, term)
    return PendingResult(
        request_id=uuid4().hex, prompt=prompt, request=request or prompt,
        language=origin.get("lang", ""), baseline=_fingerprint(turns),
        generation=term.process_generation,
        reply_session_id=origin.get("reply_session_id", ""),
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
    kind: str, term: Any, pending: PendingResult, publish: Any, *, require_report: bool = False,
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
        turns = await asyncio.to_thread(_snapshot, term)
        if not current(term, pending):
            return False
        proof = await probe(term)
        if kind in {"completed", "stopped"} and (proof is None or proof.state != expected[kind]):
            return False
        if kind == "needs_input" and proof.state != "asking" and not shows_question(term):
            return False
        last_user = next((t.text for t in reversed(turns) if t.role == "user"), "")
        matches = " ".join(last_user.split()) == " ".join(pending.prompt.split())
        fresh = bool(turns and _fingerprint(turns) != pending.baseline and matches)
        report = ""
        evidence = "terminal_state_only; task success is unverified"
        if kind == "completed" and fresh and turns[-1].role == "assistant":
            report = str(turns[-1].text or "")
            evidence = "fresh assistant message for this exact prompt; not independent verification"
        elif kind == "needs_input":
            report = "\n".join(term.transcript.tail(20))[-3000:]
            evidence = "current terminal question; not a completed task"
        elif kind == "stopped":
            report = "The CLI recorded an interrupted task; no completion is claimed."
            evidence = "explicit interruption in the current CLI session"
        elif kind in {"failed", "exited"}:
            report = f"Process state: {kind}; exit code: {getattr(term, 'exit_code', None)}"
        if require_report and not report:
            return False
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
                await publish_result(
                    kind, term, pending, publish, require_report=kind == "completed",
                )
