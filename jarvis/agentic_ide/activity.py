"""Is this pane working, waiting for you, or done?

## The question this answers

A workspace is a wall of terminals, and the one thing the wall cannot tell you
at a glance is which of them stopped. A coding CLI that finishes a job does not
announce it: the spinner row disappears, the input box comes back, and the pane
looks exactly like the eleven around it that are still thinking. So the user
watches the grid, or comes back twenty minutes later to find that everything
had been finished for nineteen of them.

## The rule: read the TERMINAL, never the product

**A pane is working while its screen keeps changing, and waiting once the
screen has stood still.** That is the whole detector, and it is deliberately a
property of the terminal rather than of whatever is running inside it. A
workspace opens five different coding CLIs today and an unknown number
tomorrow; a rule that knows what any of them prints is a rule that breaks on
the next one, in the next release, or in the next language.

Two earlier versions proved that the hard way:

* **"It draws an interrupt hint while busy."** The installed Claude Code does
  not, in the states that matter. No pane was ever seen working, so the one
  transition the feature watches for could not happen, and the bell stayed
  empty while terminals finished all around it.
* **"It ticks a bracketed clock while busy."** Codex ticks one while it THINKS
  and drops it while it WRITES, so a reply still arriving read as finished. The
  same pattern also matched the model banner every pane carries — ``Opus 5 (1M
  context)`` case-folds to ``(1m context)`` — and read every idle terminal as
  busy.

Both mistakes have the same shape: a claim about a product, checked against one
product. Screen movement makes no claim at all.

## Why this is safe, measured rather than assumed

Sampled twice a second across Claude Code, Codex, OpenCode and Kimi
(2026-07-29):

===================  ==========================================
While WAITING        0 screen changes in 40 samples, all four
While WORKING        a change every sample — longest gap 0.5 s
After finishing      still for 107 s and counting
===================  ==========================================

So the two states are not close: a working pane repaints at least twice a
second, and an idle one is perfectly still. None of these products draws its
own blinking cursor, keeps a clock in a status bar, or emits a heartbeat — any
of which would have sunk this approach, which is why it was measured first.
:data:`STILL_S` sits eight times above the longest observed gap.

## Screen CONTENT, not bytes

Bytes arriving is the weaker signal and would be the tempting one, since it
needs no state. A terminal receives plenty of bytes that change nothing a
person can see: cursor moves, a row repainted with identical characters, replies
to the emulator queries a CLI makes at startup. Fingerprinting the visible rows
answers "did anything CHANGE", which is the question. (Bytes are still used as
a fallback for a caller that has no previous screen to compare against.)

## The one exclusion

**Movement in the shadow of a keystroke is not the agent working.** A terminal
echoes what a person types, so a pane being typed into is a pane whose screen
changes; without this, writing a prompt by hand reads as a busy agent, and the
moment the user pauses to think the pane is reported finished. Keystrokes are
stamped on the pane (``Terminal.last_input_at``) and movement in their shadow
does not count.

## Honest limits

An agent whose process WEDGES stops repainting, so it reads as finished a few
seconds later; the wording ("finished and waiting at its prompt") is then
optimistic, which is the one case where this is wrong in the user's favour
rather than silent. A CLI that genuinely draws nothing at all while it works —
none of the four measured — would be reported early. A plain shell pane is not
an agent and is left out entirely by the caller.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

#: What one pane is doing, in one word.
#:
#: ``starting`` — no agent process yet (a pane waiting for a cold-start slot).
#: ``working`` — its screen is moving.
#: ``waiting`` — alive and still.
#: ``asking`` — still, AND showing a question or a choice.
#: ``failed`` — its agent could not be started.
#: ``exited`` — its process is gone.
Activity = Literal["starting", "working", "waiting", "asking", "failed", "exited"]

#: How long the screen must stand still before the pane counts as waiting.
#:
#: Eight times the longest gap measured while an agent worked (0.5 s, see the
#: module docstring), so an agent between two steps is never mistaken for one
#: that stopped — while a pane that really has finished is recognised within a
#: few seconds. The notification on top of this waits again (`SETTLE_S`), so
#: nothing is filed until a pane has been quiet for roughly ten seconds.
STILL_S = 4.0

#: How many of the pane's visible rows the fingerprint covers.
#:
#: The BOTTOM of the screen: a TUI keeps its status row, its spinner and its
#: input box there, and that is where the movement is. Fingerprinting the whole
#: screen would also work, and would additionally react to a scrollback shift
#: that changes nothing about whether the agent is busy.
TAIL_ROWS = 12

#: A visible question or choice. This does NOT decide whether a pane is busy —
#: it only chooses the WORD for a pane already established as still, so a
#: terminal waiting for an answer can say so rather than claiming it finished.
#:
#: Every entry is a phrase a TUI writes only while actually asking. Two classes
#: were tried and removed: a startup BANNER ("1 MCP server needs
#: authentication") sits on screen for the pane's whole life and would make
#: every idle terminal a standing question, and bare verbs ("confirm",
#: "approve") match an agent's ordinary prose about its own work.
#:
#: A phrase nobody has anticipated costs the user a more specific word and
#: nothing else — the entry is still filed, as "finished".
ASK_FRAGMENTS: tuple[str, ...] = (
    "do you want",
    "would you like",
    "(y/n)",
    "[y/n]",
    "yes/no?",
    "press enter to continue",
    "1. yes",
    "❯ 1.",
    "▶ 1.",
    "select an option",
    "choose an option",
)


def visible_rows(term: Any) -> list[str]:
    """The pane's bottom rows as they are on screen right now.

    Reads the replayed SCREEN rather than the cleaned transcript: the transcript
    folds a status row that repeats unchanged, and movement in that row is
    precisely the signal here.
    """
    transcript = getattr(term, "transcript", None)
    screen = getattr(transcript, "screen", None)
    try:
        rows = screen.display() if screen is not None else []
    except Exception:  # noqa: BLE001 - a test double may expose no real screen
        return []
    return [str(row) for row in rows[-TAIL_ROWS:]]


def screen_digest(term: Any) -> str:
    """A fingerprint of what this pane is showing.

    Compared against the previous one to answer "did the screen move?". Short
    and cheap: a dozen rows hashed, called once per pane per sweep.
    """
    return hashlib.sha1(  # noqa: S324 - a change detector, not a security digest
        "\n".join(visible_rows(term)).encode("utf-8", "replace")
    ).hexdigest()


def _contains(rows: Sequence[str], fragments: Sequence[str]) -> bool:
    lowered = [row.lower() for row in rows]
    return any(fragment in row for row in lowered for fragment in fragments)


def shows_question(term: Any) -> bool:
    """Is there a question or a choice on this pane's screen right now?

    The same reading :func:`read_activity` uses for its ``asking`` word, asked
    on its own — because a caller can need it while the pane is also MOVING, and
    ``read_activity`` answers "working" first (movement is the stronger signal
    for what it is for). A pane redrawing itself around a trust prompt is both,
    and a caller deciding whether to type into it needs the question half.
    """
    return _contains(visible_rows(term), ASK_FRAGMENTS)


def _typing_now(term: Any, moment: float) -> bool:
    """Is somebody at this pane's keyboard right now?

    A terminal echoes keystrokes, so movement while a person types is not the
    agent working — see the module docstring.
    """
    last_in = getattr(term, "last_input_at", None)
    if not last_in:
        return False
    since = moment - float(last_in)
    return 0 <= since <= STILL_S


def _moving(term: Any, moment: float, still_since: float | None) -> bool:
    """Has this pane's screen changed recently enough to call it busy?

    ``still_since`` is when the screen was last seen to CHANGE, tracked by the
    caller across sweeps. Without it — a caller with no previous screen to
    compare against — this falls back to "bytes arrived recently", which is the
    weaker form of the same question and errs towards "busy".
    """
    if _typing_now(term, moment):
        return False
    if still_since is None:
        last_out = getattr(term, "last_output_at", None)
        if not last_out:
            return False
        since = moment - float(last_out)
        return 0 <= since <= STILL_S
    since = moment - float(still_since)
    # A stamp in the FUTURE is not freshness — it is a clock that does not agree
    # with the caller's. Silence is the honest answer.
    return 0 <= since <= STILL_S


def read_activity(
    term: Any, *, now: float | None = None, still_since: float | None = None
) -> Activity:
    """What ``term`` is doing at this instant.

    ``still_since`` is the moment this pane's screen last changed, which the
    caller tracks across sweeps (see :func:`screen_digest`). Duck-typed on
    purpose — :class:`~.session.Terminal` imports this module's siblings — so a
    test double carrying half a pane answers with a word rather than an
    ``AttributeError``.
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

    moment = time.time() if now is None else now
    if _moving(term, moment, still_since):
        return "working"
    if _contains(visible_rows(term), ASK_FRAGMENTS):
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
    "SETTLED",
    "STILL_S",
    "TAIL_ROWS",
    "Activity",
    "is_settled",
    "read_activity",
    "screen_digest",
    "shows_question",
    "visible_rows",
]
