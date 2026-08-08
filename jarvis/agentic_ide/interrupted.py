"""The panes a restart left standing still, and the nudge that starts them again.

## The gap this closes

Resuming a workspace brings the panes back and reconnects each one to the
conversation it was having (see :mod:`.resume_store`). What it cannot do is make
the agents carry on: a coding CLI launched with ``--resume`` reads its old
transcript and then sits at its prompt. So an agent that was halfway through
rewriting a module when the app was restarted comes back holding every detail of
that job — and does nothing with it, forever, unless somebody says so.

On screen those panes look exactly like panes that finished, which is the actual
problem. The user's own report: *"the sessions were interrupted"* — twelve
terminals back in their places, none of them working, and no way to tell which
of them still had something to do.

## What counts as interrupted

Two pieces of evidence must agree. The saved pane must have been observed
actively working before its process went away, and the resumed pane must now be
idle at its prompt. A valid conversation alone proves neither: it may have
finished cleanly or be waiting for the user's answer. The activity checkpoint
is persisted with the resume snapshot; :attr:`Terminal.continuation_pending`
then carries that decision through the current process.

That matters, because the tempting heuristics are all wrong:

* **"It has printed nothing for N minutes"** — an agent thinking hard about a
  large refactor prints nothing for minutes at a time, and a stalled one may
  redraw its status bar every second. Idle time measures the TUI, not the work.
* **"Its transcript ends mid-sentence"** — a resumed pane's transcript starts
  empty. Whatever the old process printed died with its screen.
* **"It was resumed"** alone — true of every pane in a restored workspace,
  including the ones somebody has already put back to work.
* **"Its screen is moving, so it carried on by itself"** — the opposite
  mistake, and the one that broke this feature outright. A restored CLI
  repaints its banner and its old transcript for several seconds before it
  settles at a prompt; reading that as work retracted the offer for every
  restored pane within two sweeps of it coming back, so the button reported
  zero for ever. Only a submission stamped for the current process proves
  work; :attr:`Terminal.idle_seen` merely records that startup repainting has
  settled.

## What the nudge is

One short prompt, ``"continue"`` by default, typed into the pane exactly the way
the prompt bar types one — same sanitizing, same submit verification, same
honest three-state answer about whether it was accepted. This module adds no
second write path into a PTY; it decides WHICH panes to send to and reports what
happened. A caller can pass its own wording.

Deliberately not automatic. Sending "continue" to every resumed pane the moment
a workspace comes back would restart twelve agents on twelve unattended tasks
because somebody reopened a folder to look at one file, and an agent that was
interrupted for a reason ("stop, that is the wrong approach") would resume
exactly what it was told to stop doing. The offer is a button.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from .activity import read_activity, shows_question
from .fleet_actions import ready_for_prompt
from .recap import condense
from .session import SessionError, accepts_prompts

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .session import Registry, Session, Terminal

#: What a pane is told when the caller does not say. Short on purpose: the agent
#: is holding the whole conversation, so the instruction it needs is the one word
#: that means "carry on with what you were doing" in every coding CLI that has a
#: transcript to carry on from. A longer sentence would compete with the plan the
#: agent already has.
CONTINUE_PROMPT = "continue"

#: How much of the pane's last instruction travels with the offer. Enough to
#: recognise which job it was, not the whole brief — this renders in a list.
TASK_CHARS = 140


@dataclass(frozen=True, slots=True)
class InterruptedPane:
    """One pane that came back holding a conversation and was never restarted."""

    workspace_id: str
    #: The workspace tab's label, so a name identifies its folder in a list that
    #: spans several of them.
    workspace: str
    folder: str
    key: str
    name: str
    agent: str
    display_name: str
    status: str
    #: Will a "continue" reach it? False only for a pane whose agent is DEAD —
    #: an instruction cannot be typed into a terminal that exited, and saying so
    #: before the click is the point. A pane that is merely still starting is
    #: continuable: the instruction waits for it (see ``starting``).
    continuable: bool
    #: Its agent is still coming up. Continuing it is a promise kept a few
    #: seconds later, not a refusal.
    starting: bool
    #: A "continue" is already waiting to be delivered to it. What stops a second
    #: press from queueing a second one.
    queued: bool
    #: Why not, in one plain sentence. Empty when it can.
    blocked_reason: str
    #: What this pane was last asked to do, shortened. Empty when the last
    #: instruction came from the user's own keyboard rather than through Jarvis
    #: — the pane keeps no copy of what was typed into it directly.
    last_task: str
    prompts_sent: int
    #: When the current (resumed) process started. What the user reads as "since
    #: the restart".
    started_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "workspace": self.workspace,
            "folder": self.folder,
            "key": self.key,
            "name": self.name,
            "agent": self.agent,
            "display_name": self.display_name,
            "status": self.status,
            "continuable": self.continuable,
            "starting": self.starting,
            "queued": self.queued,
            "blocked_reason": self.blocked_reason,
            "last_task": self.last_task,
            "prompts_sent": self.prompts_sent,
            "started_at": self.started_at,
        }


@dataclass(frozen=True, slots=True)
class ContinueReport:
    """What sending the nudge actually did, pane by pane.

    Four lists rather than a count, because the outcomes need different words
    from whoever reports this: a pane that took the instruction, one whose agent
    is still starting and will get it shortly, one that took the text but never
    submitted it (the prompt is sitting in its input box and a human has to
    press Enter), and one that refused. Collapsing any of them into "it is
    running again" is the lie :meth:`Registry.send_prompt` goes to some length
    to avoid.
    """

    continued: list[str]
    unconfirmed: list[str]
    failed: list[tuple[str, str]]
    #: Panes whose process or input line is still starting. The instruction is
    #: held and delivered when it becomes writable — a promise, not a refusal.
    queued: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "continued": list(self.continued),
            "queued": list(self.queued),
            "unconfirmed": list(self.unconfirmed),
            "failed": [{"name": name, "detail": detail} for name, detail in self.failed],
        }


def _workspace_label(session: Session) -> str:
    """What the workspace bar calls this workspace."""
    return session.name or session.profile.name or Path(session.folder).name or session.folder


def _blocked_reason(term: Terminal) -> str:
    """Why this pane cannot be nudged right now, or an empty string.

    ``pending`` is deliberately NOT in here. A pane still waiting for a
    cold-start slot is not blocked, it is early — and treating "early" as
    "blocked" is what made the button skip terminals in a big workspace. Those
    are reported by :func:`_starting`, continued the moment they come up, and
    kept out of the refusal list entirely.
    """
    if term.status == "live" and term.pty_id:
        return ""
    if term.status == "pending":
        return ""
    if term.status == "error":
        return term.error or "Its agent could not be started."
    if term.status == "exited":
        code = term.exit_code
        ended = "finished" if code in (0, None) else f"stopped with exit code {code}"
        return f"Its agent {ended} — restart the pane before continuing it."
    return f"Its agent is not running (status: {term.status})."


def _starting(term: Terminal) -> bool:
    """Is this pane's agent still on its way up?

    Cold starts are staggered on purpose (``COLD_START_LIMIT``), so a workspace
    of a dozen panes has most of them in this state for the first few seconds
    after it appears. A spawned process also stays here until its TUI exposes a
    real input line; process existence alone is not prompt readiness.
    """
    return term.status == "pending" or (
        term.status == "live" and bool(term.pty_id) and not ready_for_prompt(term)
    )


def _describe(session: Session, term: Terminal) -> InterruptedPane:
    reason = _blocked_reason(term)
    starting = _starting(term)
    return InterruptedPane(
        workspace_id=session.id,
        workspace=_workspace_label(session),
        folder=session.folder,
        key=term.key,
        name=term.name,
        agent=term.agent,
        display_name=term.display_name,
        status=term.status,
        # A pane still starting counts as continuable: the instruction is not
        # refused, it is held and delivered when its agent comes up.
        continuable=not reason,
        starting=starting,
        queued=term.continue_when_ready,
        blocked_reason=reason,
        last_task=condense(term.last_prompt, TASK_CHARS),
        prompts_sent=term.prompts_sent,
        started_at=term.started_at,
    )


def scan(registry: Registry) -> list[InterruptedPane]:
    """Every pane, in every open workspace, that is waiting to be told to carry on.

    Across ALL workspaces rather than only the one on screen, because that is
    the shape of the problem: a restart interrupts everything at once, and a
    button that only reported the front tab would leave the other three looking
    finished. Each entry names its workspace so the list stays readable.

    In tab order, then in grid order within each workspace — the order the panes
    are on screen, so a name in the list is findable by looking at the grid.
    """
    found: list[InterruptedPane] = []
    for session in registry.sessions:
        for term in session.terminals:
            if not term.continuation_pending:
                continue
            if term.status == "live" and shows_question(term):
                # A pane asking something needs an ANSWER, not a blind
                # "continue". Read straight off the screen rather than through
                # `read_activity`, which answers "working" first — a pane can be
                # repainting itself around a trust prompt, and that is precisely
                # a pane not to type into.
                continue
            if term.status == "live" and term.idle_seen and read_activity(term) != "waiting":
                # A pane already working needs no nudge.
                #
                # Only once the pane has been SEEN standing still, though. Until
                # then its screen is moving because its CLI is drawing itself
                # back onto it — which is what a restored pane does for its
                # first few seconds, and reading that as work hid every pane
                # this list exists to show (see `Terminal.idle_seen`).
                continue
            if not accepts_prompts(term.agent):
                # A plain terminal is a shell. Jarvis does not type into one, so
                # offering to continue it would be an offer that cannot be kept
                # (see `session.accepts_prompts`). It also never resumes a
                # conversation, so this is belt and braces.
                continue
            found.append(_describe(session, term))
    return found


def _selected(
    panes: list[InterruptedPane], names: list[str] | None
) -> tuple[list[InterruptedPane], list[tuple[str, str]]]:
    """The panes a caller asked for, and a refusal for each name that is not one.

    An unknown name is reported rather than ignored: a caller that asked for
    four panes and silently got three has been told the wrong thing about what
    is now running.
    """
    if not names:
        return list(panes), []
    by_name = {pane.name.casefold(): pane for pane in panes}
    by_key = {pane.key.casefold(): pane for pane in panes}
    chosen: list[InterruptedPane] = []
    rejected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for wanted in names:
        needle = wanted.strip().casefold()
        pane = by_name.get(needle) or by_key.get(needle)
        if pane is None:
            rejected.append(
                (
                    wanted,
                    f"{wanted} is not waiting to be continued — it was never "
                    "interrupted, or somebody has already put it back to work.",
                )
            )
            continue
        if pane.key in seen:
            continue
        seen.add(pane.key)
        chosen.append(pane)
    return chosen, rejected


def _claim(
    registry: Registry, pane: InterruptedPane
) -> tuple[Session, Terminal] | None:
    """Take a pane off the waiting list BEFORE anything is sent to it.

    **The bug this fixes.** Typing a prompt and verifying it takes seconds — the
    submit is confirmed against the pane's own screen — and the flag used to be
    cleared at the END of that. So a second press while the first was still in
    flight found the pane still "waiting", and the agent received "continue"
    twice; three presses, three times. Reported exactly that way.

    Claiming is therefore the first thing that happens, in one synchronous step
    with no ``await`` in it, so two overlapping requests cannot both win. The
    caller puts the flag back when the send fails, and the pane returns to the
    list rather than being silently dropped.
    """
    session = registry.get(pane.workspace_id)
    if session is None:
        return None
    term = session.find(pane.key)
    if term is None:
        return None
    if not term.continuation_pending:
        # Somebody got here first — another press, or the user typing into the
        # pane themselves between the scan and now.
        return None
    term.continuation_pending = False
    return session, term


def _release(registry: Registry, pane: InterruptedPane) -> None:
    """Put a pane back on the waiting list after its nudge failed to go."""
    session = registry.get(pane.workspace_id)
    term = session.find(pane.key) if session is not None else None
    if term is not None:
        term.continuation_pending = True


async def continue_panes(
    registry: Registry,
    *,
    names: list[str] | None = None,
    prompt: str = CONTINUE_PROMPT,
) -> ContinueReport:
    """Tell the interrupted panes to carry on. Empty ``names`` means all of them.

    Sent through :meth:`Registry.send_prompt`, so every guarantee of the ordinary
    prompt path holds: control characters stripped, the completion popup closed
    before Enter, the submit verified against the pane's own screen, and a
    three-state answer about whether it went.

    Concurrently, because that verification watches the screen for up to a few
    seconds per pane and a grid of twelve would otherwise take the better part of
    a minute. The panes are independent processes with independent PTYs, so there
    is nothing to serialize; one pane refusing its prompt cannot affect another.

    **A pane still starting is queued, not skipped.** Cold starts are staggered
    (``COLD_START_LIMIT``), so most of a big workspace is still coming up in the
    seconds after it appears — the very window this button is pressed in. Those
    panes are marked and continued when their agent's input line is writable,
    and reported as ``queued`` rather than as done or refused.

    **Pressing twice cannot send twice.** Every pane is claimed off the waiting
    list synchronously before a single byte is typed (see :func:`_claim`), and
    put back only if its nudge could not be delivered.
    """
    text = (prompt or CONTINUE_PROMPT).strip() or CONTINUE_PROMPT
    chosen, failed = _selected(scan(registry), names)

    failed.extend((pane.name, pane.blocked_reason) for pane in chosen if not pane.continuable)

    queued: list[str] = []
    runnable: list[InterruptedPane] = []
    for pane in chosen:
        if not pane.continuable:
            continue
        claimed = _claim(registry, pane)
        if claimed is None:
            # Already claimed by a press that is still in flight. Not a failure
            # and not a second send — the first one is doing the work.
            continue
        owner, term = claimed
        if pane.starting:
            registry.defer_continue(owner, term, text)
            queued.append(pane.name)
        else:
            runnable.append(pane)

    async def _send(pane: InterruptedPane) -> tuple[InterruptedPane, Any]:
        try:
            return pane, await registry.send_prompt(
                pane.name,
                text,
                workspace_id=pane.workspace_id,
            )
        except SessionError as exc:
            return pane, exc
        except Exception as exc:  # noqa: BLE001 - one pane must not fail the batch
            logger.warning("Agentic IDE: could not continue {}: {}", pane.name, exc)
            return pane, exc

    continued: list[str] = []
    unconfirmed: list[str] = []
    for pane, outcome in await asyncio.gather(*(_send(pane) for pane in runnable)):
        if isinstance(outcome, Exception):
            # Nothing reached the agent, so this one is still waiting.
            _release(registry, pane)
            failed.append((pane.name, str(outcome) or outcome.__class__.__name__))
        elif getattr(outcome, "submitted", None) is True:
            continued.append(pane.name)
        else:
            # Typed in, not provably submitted. Its own bucket: telling the user
            # this pane is running again would be a guess, and the fix is one
            # keypress in a pane they can see. NOT released — the text is in that
            # pane's input box, and offering to type it again would put a second
            # copy behind the first.
            unconfirmed.append(pane.name)

    if continued or unconfirmed or queued or failed:
        logger.info(
            "Agentic IDE: continued {} interrupted pane(s) "
            "({} queued until they start, {} unconfirmed, {} refused)",
            len(continued),
            len(queued),
            len(unconfirmed),
            len(failed),
        )
    return ContinueReport(
        continued=continued, queued=queued, unconfirmed=unconfirmed, failed=failed
    )


__all__ = [
    "CONTINUE_PROMPT",
    "TASK_CHARS",
    "ContinueReport",
    "InterruptedPane",
    "continue_panes",
    "scan",
]
