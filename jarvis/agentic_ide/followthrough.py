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

#: How long a stopped pane may take to show its answer in its transcript before
#: the stop is reported without one. A CLI that cannot be told its session id
#: (Codex) is only found after its first prompt, on the schedule
#: ``session.CONVERSATION_DELAYS_S`` (last attempt 21.5 s after the submit); a
#: short job is long finished by then, and reporting at the first quiet sweep
#: turned its real answer into a bare "has stopped". Must outlast that schedule.
REPORT_GRACE_S = 30.0

#: How much of a long prompt's end has to match the recorded user turn. The
#: transcript reader keeps both ends of a long block (``agent_transcript._clip``,
#: 2000 characters each); this stays well inside the kept tail even when a large
#: share of it is whitespace that normalization collapses.
MATCH_TAIL_CHARS = 600


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


def _readable(term: Any) -> bool:
    """Whether a report for this pane can still appear in a local transcript."""
    from . import agent_transcript

    return not getattr(term, "computer_id", "") and agent_transcript.can_read(
        str(getattr(term, "agent", "") or "")
    )


def _normalized(text: str) -> str:
    return " ".join(str(text or "").split())


def answers_prompt(recorded: str, prompt: str) -> bool:
    """Whether the newest recorded user turn is ``prompt``.

    The transcript does not hold a prompt byte-for-byte as it was typed, so an
    exact comparison silently lost real answers:

    * a CLI writes its own user records in front of the first prompt (Codex
      opens a conversation with its AGENTS.md preamble and environment
      context), and the reader joins consecutive user records into one turn;
    * the reader strips tag-shaped text from user records (``_spoken``), which
      also removes ``<div>`` or ``List<String>`` from a coding prompt;
    * the reader keeps only both ends of a long block (``_clip``).

    So the prompt is put through the reader's own filter and must END the
    recorded turn, starting at a word boundary; a long one is compared by its
    tail, which is all the reader is sure to have kept.
    """
    from . import agent_transcript

    got = _normalized(recorded)
    expected = _normalized(agent_transcript._spoken(prompt))  # noqa: SLF001 - same package
    if not got or not expected:
        return False
    if len(expected) > MATCH_TAIL_CHARS:
        # The kept tail may begin inside a word, so no boundary is required here.
        return got.endswith(expected[-MATCH_TAIL_CHARS:])
    return got == expected or got.endswith(" " + expected)


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
    term.delegation_stopped_at = 0.0


def current(term: Any, pending: PendingResult) -> bool:
    return (
        getattr(term, "delegation_result", None) is pending
        and term.process_generation == pending.generation
        and term.last_submit_at == pending.submitted_at
    )


async def publish_result(
    kind: str,
    term: Any,
    pending: PendingResult,
    publish: Any,
    *,
    require_report: bool = False,
    now: float | None = None,
) -> bool:
    """Return an observed stop/question, never convert silence into success.

    ``False`` means nothing was published; the sweep retries while the same
    submission still owns the pane. A stop whose answer is not in the
    transcript YET is held for :data:`REPORT_GRACE_S` before it is reported
    without one.
    """
    if publish is None or not current(term, pending):
        return False
    try:
        turns = await asyncio.to_thread(_snapshot, term)
        if not current(term, pending):
            return False
        last_user = next((t.text for t in reversed(turns) if t.role == "user"), "")
        matches = answers_prompt(last_user, pending.prompt)
        fresh = bool(turns and _fingerprint(turns) != pending.baseline and matches)
        report = ""
        evidence = "terminal_state_only; task success is unverified"
        if kind == "completed" and fresh and turns[-1].role == "assistant":
            report = str(turns[-1].text or "")
            evidence = "fresh assistant message for this exact prompt; not independent verification"
        elif kind == "needs_input":
            report = "\n".join(term.transcript.tail(20))[-3000:]
            evidence = "current terminal question; not a completed task"
        elif kind in {"failed", "exited"}:
            report = f"Process state: {kind}; exit code: {getattr(term, 'exit_code', None)}"
        if require_report and not report:
            return False
        if kind == "completed" and not report and _readable(term):
            moment = time.time() if now is None else now
            stopped_at = getattr(term, "delegation_stopped_at", 0.0) or 0.0
            if not stopped_at:
                term.delegation_stopped_at = stopped_at = moment
            if moment - stopped_at < REPORT_GRACE_S:
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
            if term.reading().activity == "waiting":
                term.delegation_probe_at = moment
                await publish_result("completed", term, pending, publish, require_report=True)
