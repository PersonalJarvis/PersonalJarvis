"""Tell the user when a pane stopped needing them to watch it.

## The gap this closes

A workspace runs up to a dozen coding agents at once, in panes the size of a
postcard. Each of them works for anything between twenty seconds and half an
hour, and none of them says when it is done — the spinner row simply stops
being drawn. So the only way to know that the agent you are waiting for has
finished is to keep looking at the wall, and the actual outcome is that work
sits finished and unread for as long as it takes the user to notice.

This module watches every pane in every open workspace, notices the moment one
stops working, and files a short entry saying so. The bell in the IDE header
carries the count; the panel behind it lists the entries and jumps to the pane
that raised each one.

## What it will and will not claim

Four kinds, and each one is something the pane itself established:

* ``completed`` — it was given a job, it was working, and it is not any more.
* ``needs_input`` — it is not working and there is a question on its screen.
* ``exited`` — its process is gone (with the exit code, if there was one).
* ``failed`` — its agent could not be started at all.

Nothing here reads the agent's prose to decide whether the job went well: a
"completed" entry means the pane went quiet, never that the work is correct.
That distinction is in the wording the UI shows, because promising the second
one would be a lie the module is in no position to tell.

## Why "completed" needs a job as well as a stop

The detector underneath reads MOVEMENT (see :mod:`.activity`), and a coding CLI
moves plenty on its own account. Starting up it paints a banner, a model line
and whatever warnings it carries, then stands still — which is, frame for
frame, what an agent finishing a job looks like. So the first version of this
rang the bell once per pane the moment a workspace opened, twice for the panes
whose startup drawing arrived in two bursts, before anybody had typed a word.

A stop is therefore only reported once the pane has been GIVEN something to do:
a prompt from Jarvis, or a person pressing Enter in it
(``Terminal.last_submit_at``). The flag latches — a pane in service stays in
service — because the thing it rules out is the period BEFORE the first
instruction, not the second job. The other three kinds are unconditional: a
question, a dead process and a failed start are news whether or not the pane
ever worked.

## Why a debounce, and why it is the whole trick

A coding TUI drops its interrupt hint for a moment between steps — while it
waits for a permission answer, or repaints between tool calls. Reporting the
first frame in which the hint is absent would file "finished" several times per
minute for a pane that is working normally. So a pane has to have been settled
for :data:`SETTLE_S` before anything is filed, and the flag is armed once per
episode: an agent that goes back to work re-arms it, and one that sits at its
prompt for an hour is reported once.

## What is and is not persisted

An entry's whole value is the "jump to pane" behind it. Panes do not survive a
restart of the app, so a notification that did would point at a terminal that
no longer exists — the one thing worse than no notification. The store is a
bounded deque and is deliberately lost with the process.

The same reasoning inside one run: every sweep tells the store which panes are
still there (:meth:`NotificationCenter.retain`), and entries whose pane has been
closed go with it. A pane closed by name is cleared on the spot instead
(:meth:`NotificationCenter.forget_pane`), because its call-sign — and with it
its KEY — is handed straight back out to whatever opens next. That sweep is
also where a live pane's call-sign is refreshed, so an entry cannot go on naming
a terminal the user has since renamed.

One boolean does survive: whether a pane was observed actively working. The
resume snapshot uses that evidence to distinguish genuinely interrupted work
from a conversation that had already finished or was awaiting an answer.

## Where it runs

One sweep task per registry, started when the first workspace opens and
finishing by itself once the last one closes (AP-26: nothing new on the boot
path — with no workspace open, nothing here runs at all). The poll itself reads
each pane's replayed screen, which is a list of at most a few dozen strings, so
a dozen panes cost microseconds every couple of seconds.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from loguru import logger

from .activity import (
    RESIZE_SHADOW_S,
    STILL_S,
    WORK_CONFIRM_S,
    Activity,
    in_submit_wake,
    is_moving,
    is_settled,
    read_activity,
    screen_digest,
    stamp,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable, Mapping

    from .session import Registry

#: What one entry is about.
Kind = Literal["completed", "needs_input", "exited", "failed"]

#: How long a pane must have been settled before its stop is reported. Long
#: enough to sit out the gap a TUI leaves between two steps, short enough that
#: the bell is still news — see the module docstring.
SETTLE_S = 5.0

#: How often every pane is looked at. The read is a few string scans per pane,
#: so this is about how quickly the bell should react, not about cost.
SWEEP_INTERVAL_S = 2.0

#: Movement resuming within this long of the badge last wearing "working"
#: needs no fresh ``WORK_CONFIRM_S`` confirmation. A coding TUI pauses between
#: tool steps — a slow shell command, a long model wait — and demanding a new
#: five-second confirmation after every such gap turned one continuous job into
#: repeated false "waiting" dips. Sized to cover an inter-step gap generously
#: while staying far below the minutes a finished pane typically sits before
#: the user prints something into it (the case the confirm gate exists for).
WORK_RESUME_GRACE_S = 15.0

#: How many entries are kept. A heavy day across six workspaces produces a few
#: dozen; past this the oldest fall off the back, which is the right end to lose
#: — the panes they point at are the ones most likely to be gone.
MAX_ENTRIES = 200

#: How long the config switch is trusted before it is read from disk again.
#: ``load_config`` parses the TOML on every call, and the sweep runs every two
#: seconds; re-reading it each time would put a file read on the event loop for
#: a value that changes about once a year.
SWITCH_TTL_S = 15.0

#: How much of the pane's own recap travels with an entry — one line under the
#: headline, not a paragraph.
DETAIL_CHARS = 160

_ids = count(1)


class PaneLabel(NamedTuple):
    """What a live pane is called right now, and the tab it sits in.

    Both travel with an entry so the panel can be read without opening
    anything, and both can change under it while the entry is on screen: a pane
    can be given a new call-sign and a workspace a new tab name. Hence a label
    rather than a bare name — the entry is refreshed from this, not frozen at
    the moment it was filed.
    """

    pane: str
    workspace: str


@dataclass(frozen=True, slots=True)
class Notification:
    """One thing that happened in one pane."""

    id: str
    kind: Kind
    #: Which workspace it happened in, and what that tab is called — an entry
    #: list spans every open workspace, so a pane call-sign alone ("T3") does
    #: not identify anything.
    workspace_id: str
    workspace: str
    pane_key: str
    pane: str
    agent: str
    display_name: str
    #: One clause: what happened. Written here rather than in the client so the
    #: CLI and the UI say the same thing.
    title: str
    #: What the pane was doing, from its own recap. May be empty.
    detail: str
    created_at: float
    read: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "workspace_id": self.workspace_id,
            "workspace": self.workspace,
            "pane_key": self.pane_key,
            "pane": self.pane,
            "agent": self.agent,
            "display_name": self.display_name,
            "title": self.title,
            "detail": self.detail,
            "created_at": self.created_at,
            "read": self.read,
        }


class NotificationCenter:
    """The bounded list of entries, newest first.

    Not thread-safe and deliberately so: everything that touches it runs on the
    event loop — the sweep task that files entries and the routes that read
    them.
    """

    def __init__(self, limit: int = MAX_ENTRIES) -> None:
        self._entries: deque[Notification] = deque(maxlen=limit)

    def add(self, entry: Notification) -> Notification:
        self._entries.append(entry)
        return entry

    def list(self) -> list[Notification]:
        """Newest first — the order the panel renders."""
        return list(reversed(self._entries))

    @property
    def unread(self) -> int:
        return sum(1 for entry in self._entries if not entry.read)

    def mark_read(self, ids: Iterable[str] | None = None) -> int:
        """Mark the given entries read; without ids, every one of them.

        Returns how many CHANGED, so a caller can tell "there was nothing to
        do" from "four were cleared" instead of guessing from the new count.
        """
        wanted = None if ids is None else {str(i) for i in ids}
        changed = 0
        for index, entry in enumerate(self._entries):
            if entry.read or (wanted is not None and entry.id not in wanted):
                continue
            # Frozen on purpose — the entry is replaced rather than mutated, so
            # a list handed out earlier keeps saying what it said.
            self._entries[index] = Notification(**{**entry.to_dict(), "read": True})
            changed += 1
        return changed

    def clear(self, ids: Iterable[str] | None = None) -> int:
        """Drop the given entries; without ids, all of them."""
        if ids is None:
            dropped = len(self._entries)
            self._entries.clear()
            return dropped
        wanted = {str(i) for i in ids}
        kept = [entry for entry in self._entries if entry.id not in wanted]
        dropped = len(self._entries) - len(kept)
        self._entries.clear()
        self._entries.extend(kept)
        return dropped

    def forget_workspace(self, workspace_id: str) -> int:
        """Drop the entries of a workspace that has been closed.

        Their "jump to pane" cannot be honoured any more, and an entry that
        silently does nothing when pressed is worse than one that is gone.
        """
        return self.clear(entry.id for entry in self._entries if entry.workspace_id == workspace_id)

    def forget_pane(self, workspace_id: str, pane_key: str) -> int:
        """Drop the entries of a single pane that has just been closed.

        The sweep would get to this on its own (:meth:`retain`), but not soon
        enough: pane keys are REUSED — a new pane taking the closed one's name
        takes its key with it — so an entry left in the gap between the close
        and the next sweep would re-attach itself to a terminal it was never
        about, and "jump to pane" would go somewhere plausible and wrong.
        """
        return self.clear(
            entry.id
            for entry in self._entries
            if entry.workspace_id == workspace_id and entry.pane_key == pane_key
        )

    def retain(self, live: Mapping[tuple[str, str], PaneLabel]) -> int:
        """Keep only the entries whose pane still exists, and relabel those.

        ``live`` is every watched pane in every open workspace, keyed by
        ``(workspace_id, pane_key)`` — the sweep has just built exactly that, so
        this costs one pass over a list of at most a couple of hundred.

        Two jobs, and they are the same job: an entry has to name something that
        is still there. One whose pane has been closed is a "jump to pane"
        button that does nothing — the net under :meth:`forget_pane`, catching
        every other way a pane can disappear. One whose pane has since been
        RENAMED, or whose workspace has, is worse than useless: it names a
        terminal the user cannot find, in a tab that no longer goes by that.

        Returns how many entries were DROPPED. Relabelling is not counted: it
        keeps an entry honest rather than removing it.
        """
        kept: list[Notification] = []
        for entry in self._entries:
            label = live.get((entry.workspace_id, entry.pane_key))
            if label is None:
                continue
            if entry.pane == label.pane and entry.workspace == label.workspace:
                kept.append(entry)
                continue
            # Frozen on purpose, as in `mark_read` — replaced, never mutated.
            kept.append(
                Notification(
                    **{**entry.to_dict(), "pane": label.pane, "workspace": label.workspace}
                )
            )
        dropped = len(self._entries) - len(kept)
        if dropped:
            self._entries.clear()
            self._entries.extend(kept)
            return dropped
        # Same length, possibly different objects: replace in place so a
        # relabel is visible without disturbing the deque's bound.
        for index, entry in enumerate(kept):
            self._entries[index] = entry
        return 0


@dataclass(slots=True)
class _PaneWatch:
    """What one pane was last seen doing, and whether that was reported."""

    activity: Activity
    since: float
    #: Has this pane been working since the last entry was filed for it? What
    #: makes "it finished" different from "it has been idle all along".
    worked: bool = False
    #: Has anybody ever given this pane an instruction? A latch, never cleared
    #: while the pane lives: it rules out the stretch BEFORE the first one,
    #: where the only movement on screen is the CLI drawing itself. Without it
    #: a workspace rings its own bell once per pane the moment it opens — see
    #: the module docstring.
    tasked: bool = False
    #: Already reported for the CURRENT episode. Cleared when the pane changes
    #: state, so the next stop is reported again.
    announced: bool = False
    #: The interruption evidence last checkpointed for this pane. Kept here as
    #: well as on the terminal so an explicit prompt submission can change the
    #: live field without making the sweep forget what is already on disk.
    resume_needed: bool = False
    #: Fingerprint of what this pane was showing at the previous sweep, and when
    #: that picture last CHANGED. This pair is the detector: a screen that keeps
    #: moving is an agent at work, one that stands still is a terminal waiting
    #: (see :mod:`.activity`). Held here rather than on the terminal because it
    #: is the watcher's own bookkeeping — nothing else has any use for it.
    digest: str = ""
    changed_at: float = 0.0
    #: Process identity whose screen the fingerprint describes. A pane can
    #: survive several PTYs; reusing the previous one's digest fabricates a
    #: still observation for the replacement process.
    process_generation: int = 0
    #: When the CURRENT movement episode began, ``None`` while the pane is
    #: still. What lets the stamped word demand that movement OUTLAST a single
    #: burst (``WORK_CONFIRM_S``) before the badge spins — the raw reading
    #: calls a pane working for the whole ``STILL_S`` tail of any lone
    #: printout, slash-command output included.
    moving_since: float | None = None
    #: When the badge last actually wore "working". Movement that resumes
    #: within ``WORK_RESUME_GRACE_S`` of this is a job continuing after a
    #: tool-step pause and skips re-confirmation.
    stamped_working_at: float = 0.0


def _detail(term: Any) -> str:
    """One line about what the pane was doing, from state it already keeps."""
    text = " ".join(str(getattr(term, "last_prompt", "") or "").split())
    if not text:
        return ""
    return text if len(text) <= DETAIL_CHARS else f"{text[:DETAIL_CHARS].rsplit(' ', 1)[0]}…"


def _title(kind: Kind, term: Any) -> str:
    """What the entry says, in one clause.

    Careful about what it claims: a pane whose screen stopped moving has gone
    quiet, and that is all anybody knows. "Finished" is the honest word for
    that; "done" or "succeeded" would not be.
    """
    if kind == "completed":
        return "Finished and waiting at its prompt"
    if kind == "needs_input":
        return "Waiting for your answer"
    if kind == "failed":
        problem = " ".join(str(getattr(term, "error", "") or "").split())
        return f"Could not start — {problem}" if problem else "Its agent could not be started"
    code = getattr(term, "exit_code", None)
    if code in (0, None):
        return "Its agent exited"
    return f"Its agent exited with code {code}"


class ActivityWatcher:
    """Turns pane state transitions into notifications, one sweep at a time.

    Split from the sweep task so the whole decision — what counts as a stop,
    when it is reported, and what it is called — is a plain synchronous function
    of (panes, clock) and can be tested without a PTY, a loop, or a wait.
    """

    def __init__(self, center: NotificationCenter) -> None:
        self.center = center
        self._panes: dict[tuple[str, str], _PaneWatch] = {}
        self._resume_dirty = False

    def take_resume_dirty(self) -> bool:
        """Return and clear whether activity changed the resume checkpoint."""
        changed = self._resume_dirty
        self._resume_dirty = False
        return changed

    def forget_workspace(self, workspace_id: str) -> None:
        """Drop the per-pane memory of a workspace that is gone."""
        for key in [k for k in self._panes if k[0] == workspace_id]:
            del self._panes[key]

    def poll(
        self,
        registry: Registry,
        *,
        now: float | None = None,
        emit: bool = True,
    ) -> list[Notification]:
        """Look at every pane once and file whatever has just happened."""
        moment = time.time() if now is None else now
        filed: list[Notification] = []
        alive: dict[tuple[str, str], PaneLabel] = {}

        for session in registry.sessions:
            for term in session.terminals:
                if not _is_agent(term):
                    # A plain shell has no notion of finishing a job — it sits
                    # at a prompt for its whole life, which would file an entry
                    # the moment it is opened and never again.
                    continue
                ident = (session.id, str(getattr(term, "key", "") or ""))
                alive[ident] = PaneLabel(
                    pane=str(getattr(term, "name", "") or ""),
                    workspace=str(getattr(session, "name", "") or ""),
                )
                entry = self._step(session, term, ident, moment, emit=emit)
                if entry is not None:
                    filed.append(entry)

        # Panes that were closed take their memory with them, or a workspace
        # cycled often enough would grow this dict without bound.
        for gone in self._panes.keys() - alive.keys():
            del self._panes[gone]
        # And their entries, whose "jump to pane" has nowhere to go. Done from
        # the sweep rather than hooked onto the close path, because a pane
        # disappears in more than one way — closed singly, closed in a batch,
        # taken down with its workspace — and a hook per way is a hook missing
        # from the next one. The sweep sees whatever is actually left.
        self.center.retain(alive)
        return filed

    def _step(
        self,
        session: Any,
        term: Any,
        ident: tuple[str, str],
        now: float,
        *,
        emit: bool,
    ) -> Notification | None:
        # Whether this pane's screen is MOVING is the whole detector, and
        # movement can only be seen by comparing against the previous sweep —
        # so the fingerprint is taken first and the answer is derived from it.
        watch = self._panes.get(ident)
        process_generation = int(getattr(term, "process_generation", 0))
        digest = screen_digest(term)
        if watch is None or watch.process_generation != process_generation:
            # First sight. Nothing is filed: the pane may have been sitting
            # finished for an hour before this watcher existed, and announcing
            # that on startup would fill the bell with history.
            #
            # Its screen is recorded as having stood still for a LONG time, not
            # as having just changed, and the difference is a bug that got as
            # far as the tests: whether the screen is moving cannot be known
            # from a single look, and assuming movement makes the pane "busy"
            # for one sweep — after which it "stops", which is a finished
            # notification for every quiet terminal in the workspace. Assuming
            # stillness costs one sweep of latency for a pane that really is
            # working, and claims nothing that was not observed.
            settled_at = now - STILL_S - 1.0
            activity = read_activity(term, now=now, still_since=settled_at)
            watch = _PaneWatch(
                activity=activity,
                since=now,
                announced=True,
                tasked=_tasked(term),
                resume_needed=bool(getattr(term, "resume_continuation_needed", False)),
                digest=digest,
                changed_at=settled_at,
                process_generation=process_generation,
            )
            self._panes[ident] = watch
            # Through the same movement-confirmation gate as every later sweep:
            # a restored pane replaying its transcript is fresh output too, and
            # first sight is not a reason to trust a burst more.
            stamp(
                term,
                self._confirmed(term, watch, activity, now),
                now=now,
                since=watch.moving_since,
            )
            self._checkpoint_resume_state(term, activity, watch, now)
            return None

        if digest != watch.digest:
            watch.digest = digest
            # A changed picture is movement — unless it is the redraw a resize
            # just caused. The shadow on the pane is judged against the moment
            # the redraw ARRIVED, but this comparison only sees it at sweep
            # time, up to a full interval later — so the observation window is
            # the shadow plus one sweep, or a repaint landing between two
            # sweeps escapes whenever the next sweep runs late.
            resized_at = float(getattr(term, "last_resize_at", 0.0) or 0.0)
            if not (0 <= now - resized_at <= RESIZE_SHADOW_S + SWEEP_INTERVAL_S):
                watch.changed_at = now
        if not watch.tasked and _tasked(term):
            watch.tasked = True
        # ``now`` travels into the read as well, so a test can place a pane on
        # its own timeline instead of racing the wall clock.
        activity = read_activity(term, now=now, still_since=watch.changed_at)
        if is_settled(activity) and not is_moving(term, now, watch.changed_at):
            # An OBSERVED still screen — two looks at the same picture, never
            # the assumption the first-sight branch above has to make. This
            # proves only that restore/startup repainting has settled; a matching
            # current-process submission remains the proof of actual work.
            term.idle_seen = True

        if activity != watch.activity:
            # `worked` — the half of "completed" that says the screen moved and
            # then stopped — demands a CONFIRMED episode, judged the same way
            # the badge judges it. Raw "working" covers the STILL_S tail of any
            # lone printout, and counting that as work is how one /recap into a
            # long-finished pane still rang a "finished" bell after the badge
            # had already learned better.
            if watch.activity == "working" and self._episode_confirmed(term, watch):
                watch.worked = True
            watch.activity = activity
            watch.since = now
            watch.announced = False

        # Publish the reading before deciding whether it is worth a bell entry.
        # The pane list shows this on every pane, all the time, while an entry
        # is filed once per episode and only for the few states that are news —
        # so an early return below must never cost the UI its status.
        #
        # Publish the CONFIRMED word, not the raw one. Raw "working" covers the
        # whole STILL_S tail of any single printout — a slash command's output,
        # a menu, a redraw the user caused — and the badge spinning over an
        # agent that is sitting at its prompt is exactly the slot machine the
        # maintainer reported (2026-08-07, right after /recap printed). Real
        # work repaints continuously and sustains the episode past
        # WORK_CONFIRM_S; a burst's tail cannot. The bell shares the same
        # judgement through `worked` above, and the user's own Send is carried
        # through the confirm window by the submit grace in `observed`.
        #
        # ``since`` is the moment the movement episode actually began, so the
        # handover at the confirm boundary does not restart the badge's clock —
        # "working for 6s" must not read "for 0s" merely because the first 5
        # were spent confirming.
        stamp(
            term,
            self._confirmed(term, watch, activity, now),
            now=now,
            since=watch.moving_since,
        )
        self._checkpoint_resume_state(term, activity, watch, now)

        if watch.announced:
            return None

        kind = self._kind(activity, watch)
        if kind is None:
            return None
        # A pane that merely STOPPED has to hold still first — see the module
        # docstring on the gap a TUI leaves between two steps. A dead or broken
        # one is news immediately: there is no repaint gap to sit out, and the
        # state cannot flip back on its own.
        if is_settled(activity) and now - watch.since < SETTLE_S:
            return None

        watch.announced = True
        watch.worked = False
        if not emit:
            return None
        return self.center.add(
            Notification(
                id=f"n{next(_ids)}",
                kind=kind,
                workspace_id=str(session.id),
                workspace=str(getattr(session, "name", "") or ""),
                pane_key=ident[1],
                pane=str(getattr(term, "name", "") or ""),
                agent=str(getattr(term, "agent", "") or ""),
                display_name=str(getattr(term, "display_name", "") or ""),
                title=_title(kind, term),
                detail=_detail(term),
                created_at=now,
            )
        )

    def _confirmed(self, term: Any, watch: _PaneWatch, activity: Activity, now: float) -> Activity:
        """The word the badge wears: movement must outlast a single burst.

        Tracks when the current movement episode began and reports ``waiting``
        until it has lasted ``WORK_CONFIRM_S`` — strictly longer than the
        freshness tail one lone printout leaves behind, so a slash command's
        output can never spin the badge, while a genuinely working agent
        (repainting at least twice a second, measured) confirms within a few
        sweeps.

        Two kinds of movement need no waiting. An episode that BEGAN in the
        wake of a confirmed submission: output that soon after a Send is the
        agent starting, not the user printing something into the pane — the
        seamless handover from the submit grace. And an episode that RESUMES
        shortly after a confirmed one (:data:`WORK_RESUME_GRACE_S`): a coding
        TUI pauses between tool steps, and demanding a fresh five-second
        confirmation after every such gap turned one long job into repeated
        false "waiting" dips. The gate exists for the pane that has been SITTING
        at its prompt when a burst arrives, and such a pane has not worn
        "working" for a long time.
        """
        if activity != "working":
            watch.moving_since = None
            return activity
        if watch.moving_since is None:
            watch.moving_since = now
        if in_submit_wake(term, watch.moving_since):
            watch.stamped_working_at = now
            return activity
        if (
            now - watch.moving_since < WORK_CONFIRM_S
            and now - watch.stamped_working_at > WORK_RESUME_GRACE_S
        ):
            return "waiting"
        watch.stamped_working_at = now
        return activity

    @staticmethod
    def _episode_confirmed(term: Any, watch: _PaneWatch) -> bool:
        """Did the movement episode that just ended count as real work?

        The same standard the badge applies, asked retrospectively at the
        moment raw ``working`` flips away. Judged by how long the screen kept
        CHANGING (``changed_at - moving_since``), never by the clock at flip
        time: raw ``working`` survives the whole ``STILL_S`` tail after the
        last change, and ``STILL_S`` plus a sweep can reach ``WORK_CONFIRM_S``
        — a lone burst must not confirm itself through its own tail.
        """
        began = watch.moving_since
        if began is None:
            return False
        if in_submit_wake(term, began):
            return True
        return watch.changed_at - began >= WORK_CONFIRM_S

    def _checkpoint_resume_state(
        self, term: Any, activity: Activity, watch: _PaneWatch, now: float
    ) -> None:
        """Remember only turns proven to have been interrupted while working.

        Entering ``working`` arms the checkpoint immediately. A plain prompt
        must remain stable for the same settle window as completion notices,
        because coding TUIs briefly remove their busy row between tool steps.
        Questions clear immediately: they need the user's answer, never a blind
        continuation prompt. A resumed pane that is still offering Continue is
        preserved while it waits at its prompt.
        """
        if activity == "working":
            # `read_activity` reaches this state only after a submission stamped
            # for the live process. The offer to continue is therefore spent.
            term.continuation_pending = False
            self._set_resume_needed(term, watch, True)
            return
        if activity in {"asking", "failed"}:
            term.continuation_pending = False
            self._set_resume_needed(term, watch, False)
            return
        if activity == "exited":
            if getattr(term, "exit_code", None) in (0, None):
                term.continuation_pending = False
                self._set_resume_needed(term, watch, False)
            return
        if (
            activity == "waiting"
            and not getattr(term, "continuation_pending", False)
            and now - watch.since >= SETTLE_S
        ):
            self._set_resume_needed(term, watch, False)

    def _set_resume_needed(self, term: Any, watch: _PaneWatch, needed: bool) -> None:
        term.resume_continuation_needed = needed
        if watch.resume_needed == needed:
            return
        watch.resume_needed = needed
        self._resume_dirty = True

    @staticmethod
    def _kind(activity: Activity, watch: _PaneWatch) -> Kind | None:
        """Which entry this state deserves, or none at all."""
        if activity == "failed":
            return "failed"
        if activity == "exited":
            return "exited"
        if activity == "asking":
            # Reported whether or not it worked first: a question is the pane
            # asking for the user by name, and a CLI that asks one at startup
            # (an unauthenticated MCP server, a trust prompt) has never worked.
            return "needs_input"
        if activity == "waiting" and watch.worked and watch.tasked:
            # Both halves are load-bearing. `worked` says the screen moved and
            # then stopped; `tasked` says somebody asked for that. A starting
            # CLI painting its own banner satisfies the first and none of the
            # second, and it is the whole reason a fresh workspace used to
            # announce every pane in it as finished.
            return "completed"
        return None


def _tasked(term: Any) -> bool:
    """Has anybody given this pane an instruction it could have finished?

    Two proofs, because a pane can be driven two ways and both count. A
    timestamp from the moment something was submitted into it — by Jarvis or by
    a person pressing Enter — and the counter of prompts Jarvis has sent, which
    is the half that survives into a restored workspace where the timestamp does
    not. A pane typed into by hand before a restart therefore starts its next
    life unproven, and stays quiet until the next thing is typed into it: a
    missing notice, rather than an invented one.

    Deliberately STRICTER than :func:`~.activity.has_work_behind_it`, which the
    pane list uses for the same-looking question. That one only picks a word for
    a pane already standing still, so "it resumed a real conversation" is fair
    evidence there. This one decides whether to interrupt somebody — and a
    restored pane repainting its old transcript satisfies "was working, then
    stopped" all by itself, so accepting the same evidence here would ring the
    bell once per terminal on every restart.
    """
    if getattr(term, "last_submit_at", None):
        return True
    try:
        return int(getattr(term, "prompts_sent", 0) or 0) > 0
    except (TypeError, ValueError):  # a test double may carry anything
        return False


def _is_agent(term: Any) -> bool:
    """Does this pane run an agent, rather than being a bare shell?"""
    try:
        from .session import accepts_prompts

        return bool(accepts_prompts(str(getattr(term, "agent", "") or "")))
    except Exception:  # noqa: BLE001 - a sweep must never fail on a lookup
        return True


# --------------------------------------------------------------------- switch
_switch: tuple[float, bool] = (0.0, True)


def enabled() -> bool:
    """Is pane notification switched on for this install?

    Cached briefly (:data:`SWITCH_TTL_S`) rather than read per sweep: the value
    lives in ``jarvis.toml``, ``load_config`` parses that file on every call,
    and this runs every couple of seconds. A config that cannot be read at all
    answers "on" — the feature is additive, and failing closed here would make a
    broken config look like a broken bell.
    """
    global _switch
    cached_at, value = _switch
    now = time.monotonic()
    if now - cached_at < SWITCH_TTL_S:
        return value
    try:
        from jarvis.core.config import load_config

        value = bool(getattr(load_config().agentic_ide, "pane_notifications", True))
    except Exception:  # noqa: BLE001 - never let a config read stop the sweep
        value = True
    _switch = (now, value)
    return value


async def _enabled_off_loop() -> bool:
    """The same switch, with the TOML parse kept off the event loop.

    ``enabled()`` answers from its cache almost always; the miss parses
    ``jarvis.toml``, which is file IO plus a TOML parse on the loop that also
    carries the wake microphone — the freeze class this repo keeps re-finding
    (AP-9/AP-26). A late sweep is not only slow, it is WRONG: stamps age past
    ``STAMP_FRESH_S`` and every reader falls back to a one-look answer that
    cannot see movement. So the sweep asks through here: cache hits stay
    synchronous, and only the rare miss pays for a thread hop.
    """
    cached_at, value = _switch
    if time.monotonic() - cached_at < SWITCH_TTL_S:
        return value
    return await asyncio.to_thread(enabled)


def reset_switch_cache() -> None:
    """Forget the cached switch — for tests, and for a live config change."""
    global _switch
    _switch = (0.0, True)


# ---------------------------------------------------------------------- store
_CENTER = NotificationCenter()
_WATCHER = ActivityWatcher(_CENTER)


def center() -> NotificationCenter:
    """The process-wide store the routes read."""
    return _CENTER


def watcher() -> ActivityWatcher:
    """The process-wide watcher the sweep drives."""
    return _WATCHER


def reset() -> None:
    """Drop everything — for tests, and for a registry that was torn down."""
    _CENTER.clear()
    _WATCHER._panes.clear()  # noqa: SLF001 - same module, one owner
    _WATCHER._resume_dirty = False  # noqa: SLF001 - same module, one owner
    reset_switch_cache()


@dataclass(slots=True)
class _Sweep:
    """The one background task, and the handle that keeps it single."""

    task: asyncio.Task[None] | None = field(default=None)


_sweep = _Sweep()


async def _run(registry: Registry) -> None:
    """Poll until the last workspace closes, then finish.

    Self-terminating rather than cancelled from the outside: closing a workspace
    happens in several places, and a task that has to be stopped by each of them
    is a task that outlives one of them. With no workspace open there is nothing
    to watch, and the next ``start`` brings it back.
    """
    try:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_S)
            if not registry.sessions:
                return
            try:
                # Resume evidence is independent of the optional bell. Even
                # with notifications disabled, restored panes must not be
                # offered a blind Continue merely because history exists.
                _WATCHER.poll(registry, emit=await _enabled_off_loop())
                if _WATCHER.take_resume_dirty():
                    await registry.persist_resume_activity()
            except Exception as exc:  # noqa: BLE001 - one bad pane must not end the sweep
                logger.warning("Agentic IDE: pane notification sweep failed: {}", exc)
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        raise
    finally:
        _sweep.task = None


def start(registry: Registry) -> None:
    """Make sure the sweep is running for ``registry``. Idempotent.

    Called where a workspace is opened. Never on the boot path (AP-26): an
    install whose user has not opened the IDE runs none of this.
    """
    if _sweep.task is not None and not _sweep.task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop (a synchronous test, a CLI path). Nothing to schedule, and the
        # feature is additive — the routes still answer from an empty store.
        return
    _sweep.task = loop.create_task(_run(registry), name="agentic-ide-notifications")


def stop() -> None:
    """Cancel the sweep — for tests and for a full shutdown."""
    task = _sweep.task
    _sweep.task = None
    if task is not None and not task.done():
        task.cancel()


def watching() -> bool:
    """Is the sweep actually running right now?

    Reported alongside the entries because an empty bell has two very different
    causes — "nothing has happened" and "nobody is looking" — and they are
    indistinguishable from the outside. They were indistinguishable from the
    INSIDE too, the first time this feature reported nothing for an afternoon.
    """
    task = _sweep.task
    return task is not None and not task.done()


def state() -> dict[str, Any]:
    """Everything the bell needs in one response."""
    entries = _CENTER.list()
    return {
        "enabled": enabled(),
        "watching": watching(),
        # How many panes the sweep is holding state for. Zero while watching is
        # true means it has not completed a pass yet; it should otherwise match
        # the agent panes across every open workspace.
        "panes_watched": len(_WATCHER._panes),  # noqa: SLF001 - same module, one owner
        "unread": sum(1 for entry in entries if not entry.read),
        "notifications": [entry.to_dict() for entry in entries],
    }


__all__ = [
    "DETAIL_CHARS",
    "MAX_ENTRIES",
    "SETTLE_S",
    "SWEEP_INTERVAL_S",
    "WORK_RESUME_GRACE_S",
    "ActivityWatcher",
    "Kind",
    "Notification",
    "NotificationCenter",
    "PaneLabel",
    "center",
    "enabled",
    "reset",
    "reset_switch_cache",
    "start",
    "state",
    "stop",
    "watcher",
]
