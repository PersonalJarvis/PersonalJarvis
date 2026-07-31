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
import logging
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

    Never raises for a single pane. A pane that is missing, not running, whose
    prompt could not be written, or whose PTY write failed comes back as an
    undelivered ``Delivery`` carrying the reason.
    """
    wanted = _unique(terminals)
    if not wanted:
        return FanOutResult()

    compose_fn = compose or _default_compose
    send_fn = send or _default_send
    gate = asyncio.Semaphore(max(1, int(limit)))

    async def one(name: str) -> Delivery:
        """One pane's verdict, announced the moment it is known.

        Announced here rather than after the gather: with a fleet composing at
        once, collecting every line until the slowest pane finishes turns
        per-pane progress back into the silence it exists to remove.
        """
        delivery = await _one(name)
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
        async with gate:
            try:
                from .session import AGENT_DISPLAY

                composed = await compose_fn(
                    utterance,
                    session=session,
                    terminal_name=term.name,
                    agent_display=AGENT_DISPLAY.get(term.agent, term.agent),
                    instruction=own_instruction,
                    conversation=tuple(conversation or ()),
                )
            except Exception as exc:  # noqa: BLE001 - one pane must not sink the fleet
                log.warning(
                    "Agentic IDE fan-out: composing for %s failed", term.name,
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
        except Exception as exc:  # noqa: BLE001 - report, never propagate
            log.info("Agentic IDE fan-out: could not send to %s: %s", term.name, exc)
            return Delivery(
                terminal=term.name,
                delivered=False,
                reason_code="send_failed",
                reason=f"it did not accept the prompt ({exc})",
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
    results = await asyncio.gather(
        *(one(name) for name in wanted), return_exceptions=True
    )
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


__all__ = ["DEFAULT_CONCURRENCY", "Delivery", "FanOutResult", "deliver"]
