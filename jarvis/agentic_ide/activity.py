"""Is this pane working, waiting for you, or done?

## The question this answers

A workspace is a wall of terminals, and the one thing the wall cannot tell you
at a glance is which of them stopped. A coding CLI that finishes a job does not
announce it: the spinner row disappears, the input box comes back, and the pane
looks exactly like the eleven around it that are still thinking. So the user
watches the grid, or comes back twenty minutes later to find that everything
had been finished for nineteen of them.

This module reads one pane's CURRENT SCREEN and answers with one word. The
transitions between those words are what :mod:`.notifications` turns into
something the user can be told.

## Why the screen and not the transcript

The transcript walks the whole scrollback and folds repeated rows — the right
shape for "summarize what happened here", the wrong one for "what is on screen
right now", which is a single grid of at most a few dozen rows. The screen is
what a person looking at the pane would see, and that is the question.

## What it looks at, and what it deliberately does not

**Working** is read from the CLI's own interrupt hint. Every coding TUI
observed so far draws one while — and only while — it is busy: Claude Code
("esc to interrupt"), Codex ("Esc to interrupt"). It is the closest thing to a
protocol these products have, it means the same thing in all of them, and it is
drawn on the pane's own initiative rather than inferred by us.

**Idle time is not used**, and that is load-bearing. An agent thinking hard
about a large refactor prints nothing for minutes at a time, while a stalled
one may redraw its status bar every second — the same reasoning
:mod:`.interrupted` sets out. A silence timer would report both wrongly.

**A finished pane is not read from its text.** The rule is only ever "it was
drawing the interrupt hint and now it is not", so what the agent WROTE never
decides whether it is done. That keeps the detector working for a CLI nobody
has taught it about, in a language nobody anticipated.

The one place content is consulted is :data:`ASK_FRAGMENTS` — telling "it
finished" apart from "it is asking you something" genuinely needs to see the
question. It can only ever UPGRADE a pane that has already been established as
not-working, never claim one is busy or idle, so a phrase it has never seen
costs the user a more specific word and nothing else.

## Honest limits

A CLI that draws no interrupt hint at all reads as ``waiting`` for its whole
life, so it produces no "finished" notification rather than a wrong one. A
plain shell pane is not an agent and is left out entirely by the caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

#: What one pane is doing, in one word.
#:
#: ``starting`` — no agent process yet (a pane waiting for a cold-start slot).
#: ``working`` — its CLI is drawing an interrupt hint, so it is busy.
#: ``waiting`` — alive, not busy: sitting at its prompt.
#: ``asking`` — not busy AND showing a question or a choice.
#: ``failed`` — its agent could not be started.
#: ``exited`` — its process is gone.
Activity = Literal["starting", "working", "waiting", "asking", "failed", "exited"]

#: How many of the pane's visible rows are read. A TUI keeps its status row and
#: its input box at the BOTTOM of the screen, so the answer is always in the
#: last few rows — and a full-screen agent's upper rows are its transcript,
#: which may quote any phrase here without meaning it now.
TAIL_ROWS = 8

#: The interrupt hint a coding CLI draws while it is working, lower-cased.
#:
#: Shared rather than per-product on purpose: the wording is near-universal, and
#: splitting it per entry would mean a CLI registered tomorrow starting with an
#: empty set and never reporting that it finished anything. An entry adds its
#: own peculiarities through ``WorkspaceAgent.busy_fragments``.
BUSY_FRAGMENTS: tuple[str, ...] = (
    "to interrupt",
    "esc to stop",
    "ctrl+c to stop",
    "ctrl-c to stop",
    "to cancel",
)

#: A visible question or choice: the pane is not busy, and it is not simply
#: waiting either — somebody has to answer it before anything else happens.
#:
#: Only ever consulted for a pane already established as not-working (see the
#: module docstring), so a miss costs a word, never a wrong state.
ASK_FRAGMENTS: tuple[str, ...] = (
    "do you want",
    "would you like",
    "(y/n)",
    "[y/n]",
    "yes/no",
    "press enter to continue",
    "continue? ",
    "1. yes",
    "❯ 1.",
    "> 1.",
    "needs authentication",
    "waiting for your",
    "select an option",
    "choose an option",
    "confirm",
    "approve",
)


def _extra_busy_fragments(agent: str) -> tuple[str, ...]:
    """Whatever the registered CLI adds to the shared busy set.

    Defensive like the rest of the read path: a build without the registry, or
    an entry that predates the field, contributes nothing rather than raising
    inside a poll that runs every couple of seconds.
    """
    try:
        from jarvis.workspace.agents import get_agent

        spec = get_agent(str(agent or ""))
    except Exception:  # noqa: BLE001 - a state read must never fail on decoration
        return ()
    if spec is None:
        return ()
    return tuple(str(f).lower() for f in getattr(spec, "busy_fragments", ()) or ())


def _visible_rows(term: Any) -> list[str]:
    """The pane's bottom rows as they are on screen right now, lower-cased.

    Reads the replayed SCREEN rather than the cleaned transcript: a status row
    that repeats unchanged is folded away by the transcript, and that row is
    precisely the signal here.
    """
    transcript = getattr(term, "transcript", None)
    screen = getattr(transcript, "screen", None)
    try:
        rows = screen.display() if screen is not None else []
    except Exception:  # noqa: BLE001 - a test double may expose no real screen
        return []
    return [str(row).lower() for row in rows[-TAIL_ROWS:]]


def _contains(rows: Sequence[str], fragments: Sequence[str]) -> bool:
    return any(fragment in row for row in rows for fragment in fragments)


def read_activity(term: Any) -> Activity:
    """What ``term`` is doing at this instant.

    Duck-typed on purpose — :class:`~.session.Terminal` imports this module's
    siblings, and every attribute is read defensively so a test double carrying
    half a pane answers with a word instead of an ``AttributeError``.
    """
    status = str(getattr(term, "status", "") or "pending")
    if status == "error":
        return "failed"
    if status == "exited":
        return "exited"
    if status != "live" or not getattr(term, "pty_id", None):
        # Still waiting for a cold-start slot, or between processes. Not idle:
        # a pane that has not started has not finished anything either.
        return "starting"

    rows = _visible_rows(term)
    if _contains(rows, (*BUSY_FRAGMENTS, *_extra_busy_fragments(getattr(term, "agent", "")))):
        return "working"
    if _contains(rows, ASK_FRAGMENTS):
        return "asking"
    return "waiting"


#: The activities that mean "nobody is waiting on this pane's agent right now".
#: A transition INTO one of these, out of ``working``, is what the user wanted
#: to be told about.
SETTLED: frozenset[str] = frozenset({"waiting", "asking"})


def is_settled(activity: str) -> bool:
    """Has this pane stopped working (as opposed to never having started)?"""
    return activity in SETTLED


__all__ = [
    "ASK_FRAGMENTS",
    "BUSY_FRAGMENTS",
    "SETTLED",
    "TAIL_ROWS",
    "Activity",
    "is_settled",
    "read_activity",
]
