"""Deliver one spoken order to SEVERAL terminals of the open workspace.

The single-pane path (``BrainManager._run_agentic_ide_fast_path``) composes a
prompt and types it into one terminal. Doing that in a loop looks like the
obvious extension and is wrong twice over, which is why this is its own module:

**Latency.** Composing a prompt is a deliberate quality-tier call — 10-21 s
measured, chosen over a 2 s rewrite because the coding agent then works from
that prompt for minutes (maintainer decision 2026-07-25). A delegated voice
turn is abandoned after 20 s. Two panes composed one after another therefore
cannot fit in ANY turn, and eight cannot fit in a coffee break. Composition
runs concurrently here, bounded so a fleet of twenty does not fire twenty
provider calls into a rate limit at once.

**Honesty.** A loop that raises on the first dead pane leaves the earlier panes
briefed and the later ones untouched, with one exception to explain all of it.
A loop that swallows failures is worse: it produced the live 2026-07-26 lie,
where one of two agents was briefed and the user was told both were working.
So nothing here raises for a single pane's failure — every terminal comes back
with its own verdict, and the caller is expected to SAY the undelivered ones
out loud. ``FanOutResult`` deliberately has no boolean "ok": the interesting
state is the partial one, and a single flag hides it.

Cost: one composer call per pane and one PTY write per pane, all off the wake
path (AP-9). No LLM call is made here directly — the composer owns that, and
degrades to its deterministic prompt when no provider is reachable, so a
downloader with no API key still gets every agent briefed (§3).
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: How many prompts may be composed at the same time. The bound exists for the
#: provider, not for us: the composer resolves a quality-tier model, and twenty
#: simultaneous calls is how a fleet-wide fan-out earns a 429 that degrades
#: every pane to the deterministic prompt at once. Five keeps a realistic fleet
#: (3-8 panes) in a single wave while staying polite.
DEFAULT_CONCURRENCY = 5

#: How long one fleet order shadows a repeat of itself. The live 2026-08-11
#: 16:38 double-spawn arrived 37 s after the original order; ten minutes covers
#: the window in which the first fleet is still visibly working on it while
#: staying short enough that a genuine "run it again" later the same day works
#: without ceremony.
DUPLICATE_WINDOW_S = 600.0

#: Orders shorter than this never count as duplicates. Fleet-wide steering
#: strokes ("continue", "run the tests again") repeat legitimately within
#: minutes; a real task brief is always longer than this.
DUPLICATE_MIN_CHARS = 40

#: How similar two instructions must be to count as the same order. The router
#: brain re-dictating an order rarely reproduces it byte-for-byte (the live
#: duplicate differed by one corrected typo), so equality is the wrong test —
#: and 0.9 is far above what two genuinely different tasks reach.
DUPLICATE_SIMILARITY = 0.9


@dataclass(frozen=True, slots=True)
class RecentFanOut:
    """One fleet order a workspace has already accepted, for duplicate checks."""

    workspace_id: str
    instruction: str
    terminals: tuple[str, ...]
    at: float
    """``time.monotonic()`` when the delivery STARTED — not when it finished.

    Composition runs 10-30 s per pane, and the live duplicate arrived while the
    first fleet was still composing; a record written only on completion would
    have missed exactly the case this exists for."""


_RECENT_FANOUTS: deque[RecentFanOut] = deque(maxlen=8)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().casefold())


def record_fanout(
    session: Any, instruction: str, terminals: Sequence[str], *, now: float | None = None
) -> None:
    """Remember that this workspace just accepted a fleet order.

    Called at the START of a delivery so a duplicate arriving mid-composition
    is already visible to :func:`find_duplicate_fanout`.
    """
    workspace_id = str(getattr(session, "id", "") or "")
    text = _normalized(instruction)
    if not workspace_id or len(text) < DUPLICATE_MIN_CHARS:
        return
    _RECENT_FANOUTS.append(
        RecentFanOut(
            workspace_id=workspace_id,
            instruction=instruction,
            terminals=tuple(terminals),
            at=time.monotonic() if now is None else now,
        )
    )


def find_duplicate_fanout(
    session: Any, instruction: str, *, now: float | None = None
) -> RecentFanOut | None:
    """The recent fleet order this one repeats, or ``None``.

    A match needs the same workspace, a fresh-enough record, an essentially
    identical instruction, and at least one of the originally addressed panes
    still open — panes that are gone make a re-run legitimate, not a duplicate.
    The returned record's ``terminals`` are narrowed to the panes still alive,
    so the caller can name who is already working on it.
    """
    workspace_id = str(getattr(session, "id", "") or "")
    text = _normalized(instruction)
    if not workspace_id or len(text) < DUPLICATE_MIN_CHARS:
        return None
    moment = time.monotonic() if now is None else now
    for record in reversed(_RECENT_FANOUTS):
        if record.workspace_id != workspace_id:
            continue
        if moment - record.at > DUPLICATE_WINDOW_S:
            continue
        earlier = _normalized(record.instruction)
        if len(earlier) < DUPLICATE_MIN_CHARS:
            continue
        if difflib.SequenceMatcher(None, earlier, text).ratio() < DUPLICATE_SIMILARITY:
            continue
        alive = tuple(name for name in record.terminals if session.find(name) is not None)
        if not alive:
            continue
        return RecentFanOut(
            workspace_id=record.workspace_id,
            instruction=record.instruction,
            terminals=alive,
            at=record.at,
        )
    return None


@dataclass(frozen=True, slots=True)
class Delivery:
    """What happened to ONE addressed terminal."""

    terminal: str
    delivered: bool
    """The prompt reached this pane's input box."""

    submitted: bool | None = None
    """…and the agent accepted it and started. ``None`` when the sender does not
    report it.

    Deliberately separate from ``delivered``: a prompt ending in an ``@file``
    reference gets typed and then swallowed by the agent's completion popup, so
    it sits in the input box looking exactly like a running task (the 2026-07-25
    three-panes-one-ran failure). "Typed" and "started" are different claims and
    the readback must be able to tell them apart."""

    files: tuple[str, ...] = ()
    composed_by: str = ""
    reason_code: str = ""
    """Machine-readable failure kind: ``unknown_pane``, ``not_running``,
    ``compose_failed``, ``empty_prompt``, ``send_failed``, ``crashed``.

    The spoken layer localizes from THIS, never from ``reason`` — an English
    sentence pasted into a German answer is exactly the mixed-language output
    the per-turn resolver exists to prevent (CLAUDE.md, runtime output
    language)."""

    reason: str = ""
    """Why it was not delivered, in English, for logs and the REST/CLI surface.
    Empty on success."""

    status: str = ""
    """The pane's status when it could not be reached — the one detail worth
    speaking ("its agent is exited")."""


@dataclass(frozen=True, slots=True)
class FanOutResult:
    """Per-terminal verdicts for one fan-out, in the order they were addressed."""

    deliveries: tuple[Delivery, ...] = ()

    @property
    def delivered(self) -> tuple[Delivery, ...]:
        return tuple(d for d in self.deliveries if d.delivered)

    @property
    def undelivered(self) -> tuple[Delivery, ...]:
        return tuple(d for d in self.deliveries if not d.delivered)

    @property
    def all_delivered(self) -> bool:
        """True only when there was work AND every pane took it."""
        return bool(self.deliveries) and not self.undelivered

    @property
    def partial(self) -> bool:
        """The state a readback must never round up: some worked, some did not."""
        return bool(self.delivered) and bool(self.undelivered)

    @property
    def typed_but_not_started(self) -> tuple[Delivery, ...]:
        """Panes holding the prompt in their input box without running it."""
        return tuple(d for d in self.deliveries if d.delivered and d.submitted is False)


def _unique(terminals: Sequence[str]) -> list[str]:
    """Call-signs in the order given, without repeats.

    A spoken transcript names the same pane twice more often than one would
    think ("Iris and Bruno ... and Iris should also ..."), and briefing it
    twice submits two tasks to an agent that can only work on one.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in terminals:
        key = (name or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


async def _reserve_prompt_attachments(term: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Reserve unclaimed batches and return both batches and flat analyses."""
    lock = term.pending_prompt_attachment_lock
    async with lock:
        batches = tuple(
            batch
            for batch in term.pending_prompt_attachment_batches
            if batch.batch_id not in term.pending_prompt_attachment_reservations
        )
        term.pending_prompt_attachment_reservations.update(batch.batch_id for batch in batches)
    attachments = tuple(attachment for batch in batches for attachment in batch.attachments)
    return batches, attachments


async def _finish_prompt_attachments(term: Any, batches: Sequence[Any], *, consume: bool) -> None:
    """Commit or release reservations without touching later drops."""
    if not batches:
        return
    batch_ids = {batch.batch_id for batch in batches}
    async with term.pending_prompt_attachment_lock:
        if consume:
            term.pending_prompt_attachment_batches[:] = [
                batch
                for batch in term.pending_prompt_attachment_batches
                if batch.batch_id not in batch_ids
            ]
        term.pending_prompt_attachment_reservations.difference_update(batch_ids)


async def _finish_prompt_attachments_shielded(
    term: Any, batches: Sequence[Any], *, consume: bool
) -> None:
    """Settle a reservation even when its delivery task is being cancelled."""
    if not batches:
        return
    cleanup = asyncio.create_task(_finish_prompt_attachments(term, batches, consume=consume))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        # Shield keeps the state transition alive; wait for its lock-protected
        # commit before forwarding cancellation to the caller.
        await cleanup
        raise


#: Fan-outs whose caller was cancelled mid-delivery (voice hangup, dropped
#: HTTP client). The strong reference is what keeps a detached fan-out alive
#: until every pane has its verdict; the done-callback below is what makes
#: that verdict diagnosable from the log once nobody is left to speak it
#: (AP-30: a swallowed outcome reads as "the feature does nothing").
_DETACHED_DELIVERIES: set[asyncio.Future[Any]] = set()


def _finish_detached(work: asyncio.Future[Any], names: tuple[str, ...]) -> None:
    """Log how a detached fan-out ended — its caller can no longer report it."""
    _DETACHED_DELIVERIES.discard(work)
    if work.cancelled():
        log.warning(
            "Agentic IDE fan-out (detached): delivery for %s was cancelled "
            "before it could finish — those panes received nothing",
            ", ".join(names),
        )
        return
    exc = work.exception()
    if exc is not None:
        log.warning(
            "Agentic IDE fan-out (detached): delivery for %s failed: %r",
            ", ".join(names),
            exc,
        )
        return
    for name, result in zip(names, work.result(), strict=False):
        if isinstance(result, Delivery):
            log.info(
                "Agentic IDE fan-out (detached): %s → %s",
                result.terminal,
                "delivered" if result.delivered else (result.reason_code or "failed"),
            )
        else:
            log.warning(
                "Agentic IDE fan-out (detached): %s failed unexpectedly: %r",
                name,
                result,
            )


def note_detached_delivery(work: asyncio.Future[Any], *, describe: str) -> None:
    """Keep a caller-cancelled delivery alive and log how it ends.

    For delivery paths OUTSIDE this module's fan-out (the single-pane REST
    prompt route) that need the same survival property: the caller re-raises
    its cancellation, the work keeps the strong reference it needs to finish,
    and its outcome lands in the log because nobody is left to speak it
    (AP-30).
    """
    _DETACHED_DELIVERIES.add(work)

    def _done(task: asyncio.Future[Any]) -> None:
        _DETACHED_DELIVERIES.discard(task)
        if task.cancelled():
            log.warning(
                "Agentic IDE (detached): %s was cancelled before it finished — nothing was typed",
                describe,
            )
            return
        exc = task.exception()
        if exc is not None:
            log.warning("Agentic IDE (detached): %s failed: %r", describe, exc)
            return
        log.info(
            "Agentic IDE (detached): %s finished after its caller went away",
            describe,
        )

    work.add_done_callback(_done)
    log.warning(
        "Agentic IDE: caller cancelled mid-delivery — finishing %s detached",
        describe,
    )


#: Spoken fan-outs a hangup must be able to abort. A brief ordered by voice
#: belongs to the conversation that ordered it: ending the call ends the order
#: (maintainer decision 2026-08-13). This narrows the 2026-08-06 detach rule to
#: the REST/CLI callers it was written for — a dropped HTTP client is a lost
#: READER, not a withdrawn instruction — because on the spoken path the detach
#: produced the opposite failure: the user hung up, the workspace stayed silent
#: for another 20-30 s, and a pane they had stopped waiting for was typed into
#: (live 2026-08-13 11:19:43 hangup → 11:20:03 delivery).
#:
#: A registry rather than "cancel the caller": since the realtime session
#: transfers unfinished delegate work to process scope on hangup ("retaining it
#: for exactly-once delivery"), the caller no longer dies when the call ends, so
#: nothing downstream ever learned the conversation was over. Teardown cancels
#: these futures explicitly instead.
_SPOKEN_DELIVERIES: set[asyncio.Future[Any]] = set()


def cancel_spoken_deliveries(*, reason: str = "the call ended") -> int:
    """Abort every spoken fan-out still being written; return how many stopped.

    Called from voice-session teardown. Nothing has reached a pane at that
    point — the PTY write is the LAST step of ``deliver`` — so an aborted brief
    leaves the workspace exactly as it was: no text in the input box, no
    receipt (``last_prompt_at``), nothing for the user to discover later.

    Idempotent, never raises, and a no-op when no brief is in flight.
    """
    live = [work for work in tuple(_SPOKEN_DELIVERIES) if not work.done()]
    for work in live:
        work.cancel()
    if live:
        log.info(
            "Agentic IDE fan-out: %s — abandoned %d brief(s) still being "
            "written; nothing was typed",
            reason,
            len(live),
        )
    return len(live)


#: Panes whose brief is being written RIGHT NOW: ``(workspace_id, pane)`` →
#: ``time.monotonic()`` when it started. A counter rather than a flag, because
#: one pane can legitimately be in two fan-outs at once.
#:
#: Read by the turn context (``agentic_ide.context``) so the model answering a
#: turn is told the one fact it cannot see anywhere else: a brief is on its way
#: and NOTHING has reached that pane yet. Without it, the only workspace
#: evidence in front of the model was the receipt of EARLIER prompts, and it
#: duly narrated the pending one as finished — live 2026-08-13 11:20:12, "I have
#: prompted T5 …" spoken 2 s after dispatch, 14 s before that brief's writer had
#: even started, for a delivery that then failed outright.
_IN_FLIGHT: dict[tuple[str, str], list[float]] = {}


def in_flight_briefs(session: Any, *, now: float | None = None) -> tuple[tuple[str, float], ...]:
    """Panes of ``session`` with a brief being written, and its age in seconds.

    In-memory read of a dict, so it is free on the voice hot path (AP-9).
    """
    workspace_id = str(getattr(session, "id", "") or "")
    if not workspace_id:
        return ()
    moment = time.monotonic() if now is None else now
    out = [
        (pane, max(0.0, moment - min(starts)))
        for (wid, pane), starts in _IN_FLIGHT.items()
        if wid == workspace_id and starts
    ]
    return tuple(sorted(out, key=lambda item: item[0]))


async def _default_compose(utterance: str, **kwargs: Any) -> Any:
    from .prompt_composer import compose as compose_prompt

    return await compose_prompt(utterance, **kwargs)


async def _default_send(name: str, text: str) -> Any:
    from .session import get_registry

    return await get_registry().send_prompt(name, text)


async def deliver(
    *,
    session: Any,
    terminals: Sequence[str],
    utterance: str,
    instruction: str | None = None,
    assignments: Mapping[str, str] | None = None,
    conversation: Sequence[tuple[str, str]] | None = None,
    compose: Callable[..., Awaitable[Any]] | None = None,
    send: Callable[[str, str], Awaitable[Any]] | None = None,
    limit: int = DEFAULT_CONCURRENCY,
    include_pending_attachments: bool = False,
    on_progress: Callable[[Any], None] | None = None,
    on_delivered: Callable[[Any], Awaitable[None]] | None = None,
    cancel_on_hangup: bool = False,
) -> FanOutResult:
    """Compose and deliver a prompt for each of ``terminals``.

    ``assignments`` maps a call-sign to ITS OWN instruction and is how a split
    task reaches the fleet — each pane is then briefed on its own slice instead
    of all of them racing on the same one. Without it every pane gets
    ``instruction`` (or the raw utterance), which is the right behaviour for
    "Iris and Bruno, both of you analyse the codebase".

    ``conversation`` is the recent turns the order came out of, passed to every
    pane's composition so a back-reference ("above all points two and three")
    can be resolved into what it names. It is the same for all of them: they
    were all addressed by the same sentence.

    ``compose`` and ``send`` are injectable so this can be tested without a live
    PTY or a provider; production leaves them at their defaults.

    ``include_pending_attachments`` belongs only to the spoken Agentic-IDE
    delivery path. Manual REST/CLI fan-out must not steal a drop that was
    explicitly staged for the next spoken prompt.

    ``on_progress`` receives the composer's ``ComposeNotice`` beats for every
    pane, so a caller that is not the REST route can put the 10-30 s of writing
    on a screen too. ``on_delivered`` is awaited with the pane's ``Terminal``
    right after its prompt went in, which is how a caller announces the ARRIVAL
    without having to wait for the slowest sibling.

    Never raises for a single pane. A pane that is missing, not running, whose
    prompt could not be written, or whose PTY write failed comes back as an
    undelivered ``Delivery`` carrying the reason.

    Survives its caller by default: cancelling this call re-raises for the
    caller but lets the briefs finish detached, because a REST client that
    disconnects has lost the ANSWER, not withdrawn the order. ``deliver`` is a
    long-running effect and a reader is not its owner.

    ``cancel_on_hangup=True`` reverses that for the spoken path: the fan-out is
    registered with :func:`cancel_spoken_deliveries` and abandoned the moment
    the call ends. Hanging up ends the order — the user stopped waiting, and a
    pane typed into half a minute later is work they no longer asked for
    (maintainer decision 2026-08-13). Nothing is typed on that exit.
    """
    wanted = _unique(terminals)
    if not wanted:
        return FanOutResult()

    # Registered before composing, not after: the whole delivery runs 10-30 s
    # per pane, and the one duplicate that actually happened (2026-08-11 16:38)
    # arrived inside that window.
    record_fanout(session, instruction or utterance, wanted)

    compose_fn = compose or _default_compose
    send_fn = send or _default_send
    gate = asyncio.Semaphore(max(1, int(limit)))

    async def one(name: str) -> Delivery:
        """One pane's verdict, announced the moment it is known.

        Announced here rather than after the gather: with a fleet composing at
        once, collecting every line until the slowest pane finishes turns
        per-pane progress back into the silence it exists to remove.

        Marked in-flight per PANE, not per fan-out: with five panes composing
        at once, a ledger cleared only at the end would still be claiming
        "nothing has reached T1" minutes after T1 was typed into.
        """
        found = session.find(name) if session is not None else None
        key = (
            str(getattr(session, "id", "") or ""),
            str(getattr(found, "name", "") or name),
        )
        started = time.monotonic()
        if key[0]:
            _IN_FLIGHT.setdefault(key, []).append(started)
        try:
            delivery = await _one(name)
        finally:
            # Truthiness, not `is not None`: an empty list left behind by a
            # sibling must not make pop() raise inside a finally clause.
            starts = _IN_FLIGHT.get(key)
            if starts:
                if started in starts:
                    starts.remove(started)
                else:  # pragma: no cover - defensive, clocks are monotonic
                    starts.pop()
            if not _IN_FLIGHT.get(key):
                _IN_FLIGHT.pop(key, None)
        try:
            from .prompt_composer import announce_delivery

            announce_delivery(
                delivery.terminal,
                delivered=delivery.delivered,
                submitted=delivery.submitted,
                reason=delivery.reason,
            )
        except Exception:  # noqa: BLE001 - a progress line never costs a delivery
            log.debug("Agentic IDE fan-out: could not announce %s", name, exc_info=True)
        return delivery

    async def _one(name: str) -> Delivery:
        term = session.find(name) if session is not None else None
        if term is None:
            return Delivery(
                terminal=name,
                delivered=False,
                reason_code="unknown_pane",
                reason="there is no terminal by that name in this workspace",
            )
        # A plain terminal is a shell, not an agent — Jarvis does not type into
        # one (see session.accepts_prompts). Checked here as well as in the
        # sender so naming one in a fleet order costs neither a composition nor
        # an exception, and comes back as a plain sentence the readback can say.
        from .session import accepts_prompts

        if not accepts_prompts(str(getattr(term, "agent", "") or "")):
            return Delivery(
                terminal=term.name,
                delivered=False,
                reason_code="not_an_agent",
                reason="it is a plain terminal, so Jarvis does not type into it",
            )
        # Status and PTY are checked BEFORE composing: a prompt for a dead pane
        # costs a full quality-tier call and can never be typed anywhere.
        status = str(getattr(term, "status", "") or "unknown")
        if status != "live" or not getattr(term, "pty_id", None):
            return Delivery(
                terminal=term.name,
                delivered=False,
                reason_code="not_running",
                reason=f"its agent is {status}, not running",
                status=status,
            )

        own_instruction = (assignments or {}).get(term.name) or instruction
        pending_batches: tuple[Any, ...] = ()
        pending_attachments: tuple[Any, ...] = ()
        async with gate:
            # Reserve after acquiring the concurrency slot so cancellation
            # while waiting cannot leave an invisible batch stuck as reserved.
            if include_pending_attachments:
                pending_batches, pending_attachments = await _reserve_prompt_attachments(term)
            try:
                from .session import AGENT_DISPLAY

                # Passed only when there IS a sink: the composer defaults to its
                # own stdout notice, and injected test/CLI composers are free to
                # take no such keyword at all.
                progress_kw = {} if on_progress is None else {"on_progress": on_progress}
                composed = await compose_fn(
                    utterance,
                    session=session,
                    terminal_name=term.name,
                    agent_display=AGENT_DISPLAY.get(term.agent, term.agent),
                    instruction=own_instruction,
                    conversation=tuple(conversation or ()),
                    attachments=pending_attachments,
                    **progress_kw,
                )
            except asyncio.CancelledError:
                await _finish_prompt_attachments_shielded(term, pending_batches, consume=False)
                raise
            except Exception as exc:  # noqa: BLE001 - one pane must not sink the fleet
                await _finish_prompt_attachments_shielded(term, pending_batches, consume=False)
                log.warning(
                    "Agentic IDE fan-out: composing for %s failed",
                    term.name,
                    exc_info=True,
                )
                return Delivery(
                    terminal=term.name,
                    delivered=False,
                    reason_code="compose_failed",
                    reason=f"its prompt could not be written ({exc})",
                )

        text = getattr(composed, "text", "") or ""
        if not text.strip():
            await _finish_prompt_attachments_shielded(term, pending_batches, consume=False)
            # An empty prompt would submit a bare Enter into the agent, which
            # reads as "the user pressed return" and can re-run its last task.
            return Delivery(
                terminal=term.name,
                delivered=False,
                reason_code="empty_prompt",
                reason="its prompt came back empty",
            )

        try:
            sent = await send_fn(term.name, text)
        except asyncio.CancelledError:
            await _finish_prompt_attachments_shielded(term, pending_batches, consume=False)
            raise
        except Exception as exc:  # noqa: BLE001 - report, never propagate
            await _finish_prompt_attachments_shielded(term, pending_batches, consume=False)
            log.info("Agentic IDE fan-out: could not send to %s: %s", term.name, exc)
            return Delivery(
                terminal=term.name,
                delivered=False,
                reason_code="send_failed",
                reason=f"it did not accept the prompt ({exc})",
            )

        await _finish_prompt_attachments_shielded(term, pending_batches, consume=True)

        if on_delivered is not None:
            try:
                await on_delivered(term)
            except Exception:  # noqa: BLE001 - a notification never costs a delivery
                log.debug(
                    "Agentic IDE fan-out: could not announce the send for %s",
                    term.name,
                    exc_info=True,
                )

        # The sender reports whether the agent actually STARTED. A sender that
        # says nothing leaves it None (unknown) rather than claiming success —
        # the readback then stays silent about it instead of overstating.
        submitted = getattr(sent, "submitted", None)
        return Delivery(
            terminal=term.name,
            delivered=True,
            submitted=None if submitted is None else bool(submitted),
            files=tuple(getattr(composed, "files", ()) or ()),
            composed_by=str(getattr(composed, "composed_by", "") or ""),
        )

    # return_exceptions is deliberate belt-and-braces: `one` already contains
    # every failure it can foresee, and an unforeseen one must still not cancel
    # the siblings mid-write.
    work = asyncio.ensure_future(
        asyncio.gather(*(one(name) for name in wanted), return_exceptions=True)
    )
    if cancel_on_hangup:
        _SPOKEN_DELIVERIES.add(work)
        work.add_done_callback(_SPOKEN_DELIVERIES.discard)
    try:
        results = await asyncio.shield(work)
    except asyncio.CancelledError:
        if work.cancelled():
            raise
        if cancel_on_hangup:
            # The spoken caller is gone (teardown cancelled the retained
            # delegate task, a turn was abandoned). The order goes with the
            # conversation instead of typing into panes nobody is waiting on.
            # The strong reference and the callback stay for the unwind only:
            # the attachment reservations settle inside it, and the honest
            # "those panes received nothing" line lands in the log (AP-30).
            _DETACHED_DELIVERIES.add(work)
            work.add_done_callback(
                lambda task, names=tuple(wanted): _finish_detached(task, names)
            )
            work.cancel()
            log.info(
                "Agentic IDE fan-out: the spoken caller went away — abandoning "
                "the brief(s) for %s; nothing was typed",
                ", ".join(wanted),
            )
            raise
        # The CALLER died mid-delivery — a disconnected HTTP client, a cancelled
        # CLI — not the fleet. The order was accepted before the composer
        # started, and cancelling here is how "prompt T1 …" ended as a pane
        # that never received anything while the user believed it had (live
        # 2026-08-06 18:49/18:52: both turns were cancelled inside a 15-20 s
        # subscription-CLI composition and nothing was ever typed). Losing the
        # reader drops the verdict, not the order; the briefs still land and the
        # receipt (`last_prompt_at`) stays the proof. The SPOKEN path takes the
        # branch above instead — there the reader is the person who ordered it.
        _DETACHED_DELIVERIES.add(work)
        work.add_done_callback(lambda task, names=tuple(wanted): _finish_detached(task, names))
        log.warning(
            "Agentic IDE fan-out: caller cancelled mid-delivery — finishing "
            "the briefs for %s detached",
            ", ".join(wanted),
        )
        raise
    deliveries: list[Delivery] = []
    for name, result in zip(wanted, results, strict=True):
        if isinstance(result, Delivery):
            deliveries.append(result)
            continue
        log.warning("Agentic IDE fan-out: %s failed unexpectedly: %r", name, result)
        deliveries.append(
            Delivery(
                terminal=name,
                delivered=False,
                reason_code="crashed",
                reason=f"it failed ({result})",
            )
        )

    out = FanOutResult(deliveries=tuple(deliveries))
    log.info(
        "Agentic IDE fan-out: %d of %d delivered (%s)",
        len(out.delivered),
        len(out.deliveries),
        ", ".join(d.terminal for d in out.delivered) or "none",
    )
    return out


__all__ = [
    "DEFAULT_CONCURRENCY",
    "DUPLICATE_MIN_CHARS",
    "DUPLICATE_SIMILARITY",
    "DUPLICATE_WINDOW_S",
    "Delivery",
    "FanOutResult",
    "RecentFanOut",
    "cancel_spoken_deliveries",
    "deliver",
    "find_duplicate_fanout",
    "in_flight_briefs",
    "record_fanout",
]
