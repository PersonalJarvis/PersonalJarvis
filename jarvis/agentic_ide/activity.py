"""Is this pane working, waiting for you, or done?

## The question this answers

A workspace is a wall of terminals, and the one thing the wall cannot tell you
at a glance is which of them stopped. A coding CLI that finishes a job does not
announce it: the spinner row disappears, the input box comes back, and the pane
looks exactly like the eleven around it that are still thinking. So the user
watches the grid, or comes back twenty minutes later to find that everything
had been finished for nineteen of them.

## The rule: read the TERMINAL, never the product

**A pane is working while its screen keeps changing after the current process
received an instruction, and waiting once the screen has stood still.** Both
halves are required. Movement remains a property of the terminal rather than
of whichever product is running inside it, while the submit stamp prevents a
CLI's own background redraws from inventing work nobody requested.

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

## Two signals, and why BOTH are needed

**Either fresh output or a changed screen means working.** The first version
used the screen alone, on the reasoning that a terminal receives plenty of bytes
that change nothing a person can see — cursor moves, a row repainted with
identical characters, replies to the emulator queries a CLI makes at startup —
so fingerprinting the visible rows answers the sharper question.

That reasoning was right about the noise and wrong about the cost. The
fingerprint covers the BOTTOM :data:`TAIL_ROWS` rows, and a pane whose visible
change happens anywhere else — an agent streaming its answer into a tall pane,
a CLI whose busy row sits above the input box, a screen scrolling under a static
footer — is a pane working in a blind spot. Measured on the maintainer's own
workspace of eleven panes (2026-07-31), that read finished agents and working
ones the same way, and the badge on half the list was wrong.

The measurement that settles which signal to trust: in that same workspace,
every RESTING pane had produced no output at all for between 19 and 69 seconds,
and every WORKING pane had produced some within the last tenth of a second.
There is no ambiguous middle, so bytes answer the question on their own — while
the screen fingerprint stays as the second signal, because it survives one
sweep longer than the byte stamp and holds the answer steady across the gap.

That measurement is useful but not a universal contract. Claude Code can emit
background terminal traffic for an MCP authentication warning before anybody
gives the pane a task. Movement therefore becomes ``working`` only after a
submission to the current process; a CLI repainting its own idle state cannot
invent work. The rule remains provider-neutral because the proof is input
history, never wording on the screen.

## The two exclusions

**Movement in the shadow of a keystroke is not the agent working.** A terminal
echoes what a person types, so a pane being typed into is a pane whose screen
changes; without this, writing a prompt by hand reads as a busy agent, and the
moment the user pauses to think the pane is reported finished. Keystrokes are
stamped on the pane (``Terminal.last_input_at``) and movement in their shadow
does not count.

**Movement in the shadow of a resize is not the agent working either.** A
full-screen TUI answers a size change by redrawing its whole frame, and that
redraw is real output AND a changed screen — both signals at once. Sizes change
constantly in ordinary use: switching between the grid and the chat view,
maximizing a pane, dragging a seam, switching workspace tabs (whose reconnect
deliberately nudges a repaint), resizing the window. Every one of those made
every FINISHED pane in the workspace read as "working" for a few seconds — at
exactly the moment the user was looking at the list to see what had finished
(maintainer report 2026-08-01). Resizes are stamped on the pane
(``Terminal.last_resize_at``) and movement in their shadow does not count; an
agent that really is working keeps producing output after the shadow passes and
is read as working again within a couple of seconds.

## The moment after Send — the movement rule's one blind window

Movement proves work, but it arrives LATE at exactly the moment the user is
looking: right after they submit. An agent can take several seconds to produce
its first byte, and movement in the shadow of the submitting keystroke is
deliberately discounted (see the exclusions below) — so a pane that was just
handed work read as "waiting", which under a freshly pressed Send looks like a
send that silently failed (maintainer report 2026-08-07, repeated after the
badge's poll was made fast — the lag was here, not in the poll).

The submission itself is the missing evidence. It is stamped only when an
instruction verifiably reached the live process (:func:`_has_current_instruction`
— injected prompts confirm on screen, a hand-pressed Enter is verified before
it stamps), so for :data:`SUBMIT_GRACE_S` after the stamp a still pane is
reported ``working`` by :func:`observed`. A question on screen still outranks
the grace — a CLI that answers a submit with a permission prompt needs the
user, not a spinner — and the grace lives in :func:`observed` rather than in
:func:`read_activity` on purpose: the notification sweep keeps the strict
movement rule, or a prompt an agent swallowed would ring the bell "finished"
ten seconds after nothing happened.

## Honest limits

An agent whose process WEDGES stops repainting, so it reads as finished a few
seconds later; the wording ("finished and waiting at its prompt") is then
optimistic, which is the one case where this is wrong in the user's favour
rather than silent. A CLI that genuinely draws nothing at all while it works —
none of the four measured — would be reported early. A plain shell pane is not
an agent and is left out entirely by the caller.

## Who reads this, and why it is stamped on the pane

Two callers, one reading. The notification sweep (:mod:`.notifications`) is the
one that can actually SEE movement — it compares this sweep's fingerprint with
the last one — and it runs every couple of seconds for as long as a workspace is
open. Everything else that wants to know what a pane is doing (the workspace
state, the recap poll behind the pane list) is a request handler with a single
look and no history, and a single look cannot tell a still screen from a moving
one.

So the sweep :func:`stamp`\\ s what it observed onto the pane and the request
handlers read it back (:func:`observed`). A stamp older than
:data:`STAMP_FRESH_S` is not trusted: that is not a stale reading, it is
evidence that nothing is watching this pane at all — the sweep never started, or
its task died — and the caller gets a fresh single-look answer instead of a word
that stopped being true minutes ago.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

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

#: How long after a PTY resize any movement is read as the redraw the resize
#: caused rather than as the agent working.
#:
#: A TUI answers a size change within a few hundred milliseconds (the repaint
#: nudge waits 0.08 s between its two sizes and the paint follows immediately),
#: so two seconds cover the slowest observed redraw with room to spare — while
#: staying far below any real job. The cost is bounded and visible: an agent
#: genuinely working through a resize reads as "waiting" for at most this long,
#: then its next output falls outside the shadow and it reads as working again.
#: The silent failure this replaces was the reverse and unbounded in number:
#: every layout change relabelled every finished pane "working".
RESIZE_SHADOW_S = 2.0

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


def _fresh(moment: float, at: float | None) -> bool:
    """Did ``at`` happen inside the window that counts as "just now"?

    A stamp in the FUTURE is not freshness — it is a clock that does not agree
    with the caller's. Silence is the honest answer.
    """
    if not at:
        return False
    return 0 <= moment - float(at) <= STILL_S


def _resize_shadowed(term: Any, at: float | None) -> bool:
    """Did ``at`` fall in the shadow of this pane's last resize?

    Movement stamped there is the TUI redrawing its frame for the new geometry,
    not the agent working — see the module docstring. Asked about the moment the
    MOVEMENT happened (the output stamp, the screen-change stamp), never about
    "now": the redraw arrives within the shadow, but its stamp stays fresh for
    :data:`STILL_S` beyond it, and judging by "now" would let that tail through.
    """
    resized_at = getattr(term, "last_resize_at", None)
    if not resized_at or not at:
        return False
    return 0 <= float(at) - float(resized_at) <= RESIZE_SHADOW_S


def is_moving(term: Any, moment: float, still_since: float | None) -> bool:
    """Is anything happening in this pane right now?

    Either signal is enough (see the module docstring): output that has just
    arrived, or a screen seen to CHANGE — the latter tracked by the caller
    across sweeps and passed in as ``still_since``, since a single look cannot
    see movement. A caller without that history simply asks the first question.
    Movement in the shadow of a keystroke or a resize counts for neither.
    """
    if _typing_now(term, moment):
        return False
    out_at = getattr(term, "last_output_at", None)
    if _fresh(moment, out_at) and not _resize_shadowed(term, out_at):
        return True
    return _fresh(moment, still_since) and not _resize_shadowed(term, still_since)


def _has_current_instruction(term: Any) -> bool:
    """Did somebody submit work to the agent process that is live now?

    Historical prompt counters, resumed conversations, and prior idle periods
    prove that a pane has been used, but not that its replacement process is
    currently working. The generation pair is stamped on manual, injected, and
    Continue submissions and is reset by spawning a new process, so it is the
    narrow proof required before movement may become a live ``working`` claim.
    """
    if not getattr(term, "last_submit_at", None):
        return False
    try:
        return int(getattr(term, "submit_generation", -1)) == int(
            getattr(term, "process_generation", 0)
        )
    except (TypeError, ValueError):  # Malformed legacy counters mean no matching run.
        return False


def read_activity(
    term: Any, *, now: float | None = None, still_since: float | None = None
) -> Activity:
    """What ``term`` is doing at this instant — the RAW movement reading.

    Raw means: no submit grace, no burst confirmation. This is the word for
    callers that want the movement rule itself (the bell, a caller deciding
    whether it is safe to type into the pane); the word the badge SHOWS is
    :func:`observed`'s, and a caller reaching here for display will spin on a
    lone printout.

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
    if _has_current_instruction(term) and is_moving(term, moment, still_since):
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


#: How long a stamped reading is trusted by a caller that did not take it.
#:
#: The sweep behind it runs every two seconds
#: (``notifications.SWEEP_INTERVAL_S``), so three sweeps of slack absorbs a busy
#: event loop while keeping the "nothing is watching" case honest — see the
#: module docstring.
STAMP_FRESH_S = 6.0

#: How long a fresh submission may claim ``working`` before movement confirms.
#:
#: Long enough to cover the slowest observed gap between a submit and the
#: agent's first paint, short enough that a prompt an agent truly swallowed
#: goes back to telling the truth within one glance at the list. Applied only
#: on top of a ``waiting`` reading and only for a submission stamped for the
#: LIVE process — see the module docstring.
SUBMIT_GRACE_S = 10.0

#: How long movement must LAST before the badge calls a pane working.
#:
#: The inverse problem of the submit grace, reported the same day: any single
#: printout into a once-instructed pane — a slash command's output, a menu, a
#: redraw the user caused — kept the raw reading on ``working`` for the whole
#: :data:`STILL_S` freshness tail, so the badge spun over an agent that was
#: sitting at its prompt (the maintainer watched it spin right after ``/recap``
#: printed). Real work is not a burst: every measured CLI repaints at least
#: twice a second for as long as it is busy, so genuine work sustains movement
#: indefinitely while a burst's tail dies after ``STILL_S``. Strictly above
#: ``STILL_S`` for exactly that reason — a lone burst can never outlive it.
#:
#: The cost is bounded and covered: a pane that starts working WITHOUT a fresh
#: submit shows its spinner this many seconds late, and the one start a user
#: actually watches — their own Send — is carried by :data:`SUBMIT_GRACE_S`,
#: which outlasts this window. The notification sweep keeps the raw reading;
#: this gate is applied to the STAMPED word only (see ``notifications._step``).
WORK_CONFIRM_S = 5.0


class Reading(NamedTuple):
    """What a pane is doing, and when it started doing it.

    ``since`` is 0 for an answer derived from one look, which has no way of
    knowing how long the pane has been in this state — never a claim that it
    started at the epoch.

    The empty activity is a real answer and means "this vocabulary does not
    describe this pane" — see :data:`NO_READING`.
    """

    activity: Activity | Literal[""]
    since: float


#: The answer for a pane that has no job to be in the middle of: a plain shell,
#: which runs no agent at all.
#:
#: Its own value rather than ``waiting``, because every word here is a claim
#: about a JOB — and "waiting" on a shell prompt would read as an agent that has
#: finished one. A caller that gets this shows whatever it showed before this
#: feature existed.
NO_READING = Reading("", 0.0)


def stamp(term: Any, activity: Activity, *, now: float, since: float | None = None) -> None:
    """Publish what this sweep observed, for readers that cannot observe.

    Only the transition is timed: re-stamping the same word every two seconds
    must not keep resetting "since", or a pane that has been waiting for twenty
    minutes would always look like it just stopped.

    ``since`` lets the caller backdate a transition to when the state actually
    began. The confirm gate stamps "working" only once movement has outlasted
    ``WORK_CONFIRM_S`` — the honest start of that episode is when the movement
    began, not the moment the gate was satisfied, and without this every
    confirmed episode reported "For 0s" about work already seconds old. Clamped
    to ``now``: a stamp may be late, never early.
    """
    if getattr(term, "activity", "") != activity:
        term.activity_since = now if since is None else min(float(since), now)
    term.activity = activity
    term.activity_at = now


def in_submit_wake(term: Any, moment: float) -> bool:
    """Did ``moment`` fall inside the wake of a confirmed submission?

    True while ``moment`` is within :data:`SUBMIT_GRACE_S` of an instruction
    that verifiably reached the LIVE process. The window where a still screen
    may claim ``working`` (:func:`observed`), and where movement needs no
    confirmation before the badge spins (``notifications._step``) — output
    arriving this soon after a submit is the agent starting, not the user
    printing something into the pane.
    """
    if not _has_current_instruction(term):
        return False
    at = float(getattr(term, "last_submit_at", 0.0) or 0.0)
    return 0 <= moment - at <= SUBMIT_GRACE_S


def _submit_graced(term: Any, reading: Reading, moment: float) -> Reading:
    """``working`` for the first seconds after a submit, before movement shows.

    Upgrades only a ``waiting`` reading, so a question on screen keeps saying
    "needs you" and a broken or exited pane keeps saying so. ``since`` is the
    submit stamp — the honest moment this working episode began.
    """
    if reading.activity != "waiting":
        return reading
    if not in_submit_wake(term, moment):
        return reading
    return Reading("working", float(getattr(term, "last_submit_at", 0.0) or 0.0))


def observed(term: Any, *, now: float | None = None) -> Reading:
    """What ``term`` is doing, for a caller with no history of its own.

    The sweep's reading while there is a fresh one, and a single-look answer
    otherwise — either way graced for a just-submitted pane (see the module
    docstring: the sweep's strict movement rule goes blind for a few seconds
    at exactly the moment the user has just pressed Send). Duck-typed like the
    rest of this module: a pane that has never been stamped answers from the
    look, not with an ``AttributeError``.
    """
    moment = time.time() if now is None else now
    word = str(getattr(term, "activity", "") or "")
    at = float(getattr(term, "activity_at", 0.0) or 0.0)
    if word and 0 <= moment - at <= STAMP_FRESH_S:
        since = float(getattr(term, "activity_since", 0.0) or 0.0)
        reading = Reading(word, since)  # type: ignore[arg-type]
    else:
        # The stampless fallback is the RAW single look: with no sweep history
        # it cannot demand that movement outlast a burst, so a lone printout
        # can read as working here. Rare by construction — the sweep runs for
        # as long as any workspace is open.
        reading = Reading(read_activity(term, now=moment), 0.0)
    return _submit_graced(term, reading, moment)


def has_work_behind_it(term: Any) -> bool:
    """Is this pane's stillness a FINISHED job, or an untouched terminal?

    Both look identical on screen — a prompt and nothing moving — and they are
    not the same news, so the word for them must not be the same either.

    Three proofs, because a pane can be driven three ways:

    * something was submitted into it, by Jarvis or by a person pressing Enter;
    * Jarvis has sent it prompts (the half that survives a restored workspace
      where the timestamp does not);
    * or it CONTINUED an existing conversation when its process started, which
      is only ever true of a conversation that really exists on disk — the
      resume path checks (``has_conversation``) before claiming it, because a
      pane opened and never used leaves nothing behind to continue.

    That third one is deliberately NOT the same as "it holds a conversation
    id": every pane is handed one at launch, used or not, so an id proves
    nothing about whether anybody has spoken to it.

    The third proof is why this is not the same question as the bell's
    ``notifications._tasked``, which deliberately answers only the first two.
    That one decides whether to ANNOUNCE a completion, and a restored pane
    repainting itself would otherwise ring the bell for every terminal in the
    workspace on every restart. This one only chooses a word for a pane already
    standing still, where "it resumed a real conversation" is exactly the
    evidence that its quiet screen is a finished job.

    Getting that wrong is what this fixes: after a restart, every pane driven by
    hand — the maintainer's whole workspace — was labelled "idle" next to a
    recap saying it had worked for ten minutes.
    """
    if getattr(term, "last_submit_at", None):
        return True
    try:
        if int(getattr(term, "prompts_sent", 0) or 0) > 0:
            return True
    except (TypeError, ValueError):  # a test double may carry anything
        pass
    return bool(getattr(term, "resumed", False))


__all__ = [
    "ASK_FRAGMENTS",
    "NO_READING",
    "RESIZE_SHADOW_S",
    "SETTLED",
    "STAMP_FRESH_S",
    "STILL_S",
    "SUBMIT_GRACE_S",
    "WORK_CONFIRM_S",
    "TAIL_ROWS",
    "Activity",
    "Reading",
    "has_work_behind_it",
    "in_submit_wake",
    "is_moving",
    "is_settled",
    "observed",
    "read_activity",
    "screen_digest",
    "shows_question",
    "stamp",
    "visible_rows",
]
