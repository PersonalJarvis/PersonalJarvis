"""Task lifecycle with terminal movement as a fallback observation.

The sweep reads structured CLI events off the event loop. An unfinished task
stays working through silent tools and model waits; completion and interruption
need explicit events. Missing evidence remains unknown. Movement and visible
permission prompts still describe providers without a readable task record.

The helpers below debounce terminal repainting for the initial observation and
legacy callers. Their stillness threshold is never proof of task completion.
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
Activity = Literal[
    "starting", "working", "waiting", "asking", "failed", "exited", "stopped", "unknown",
]

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
    if _adopted_with_work(term):
        return True
    if not getattr(term, "last_submit_at", None):
        return False
    try:
        return int(getattr(term, "submit_generation", -1)) == int(
            getattr(term, "process_generation", 0)
        )
    except (TypeError, ValueError):  # Malformed legacy counters mean no matching run.
        return False


def _adopted_with_work(term: Any) -> bool:
    """Is this the process re-joined after an app restart, already on a job?

    An agent that kept running in the PTY host through an app restart received
    its instruction in the previous app's lifetime, so no submit stamp of THIS
    app can prove it. ``Session._adopt_hosted`` records the proof it had then
    (a conversation on disk, prompts sent, work seen at the last checkpoint) as
    ``adopted_generation``; it holds only for that very process. A process
    resumed to finish a turn cut off mid-work carries the same proof: it goes
    on with the old job by itself, with nothing submitted in this lifetime.
    """
    try:
        return int(getattr(term, "adopted_generation", -1)) == int(
            getattr(term, "process_generation", 0)
        )
    except (TypeError, ValueError):
        # A garbled generation counter just means no adoption to report.
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
    from .task_state import evidence

    proof = evidence(term, now=moment)
    if proof is not None:
        if shows_question(term):
            return "asking"
        states = {"working": "working", "completed": "waiting", "stopped": "stopped",
                  "asking": "asking", "failed": "failed"}
        if proof.state in states:
            return states[proof.state]
        if _has_current_instruction(term) and is_moving(term, moment, still_since):
            return "working"
        return "unknown" if has_work_behind_it(term) else "waiting"
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
    from .task_state import evidence

    proof = evidence(term, now=moment)
    if proof is not None and proof.state == "completed":
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
    from .task_state import evidence

    proof = evidence(term)
    if proof is not None and proof.state != "unknown":
        return True
    if getattr(term, "last_submit_at", None):
        return True
    if _adopted_with_work(term):
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
