"""Episode data classes for the L2 Story Tracker.

``Episode`` is the immutable persist form (frozen+slots) that the
``StoryTracker`` writes to SQLite after a Verdichter call. Immutability
allows the flight recorder to replay episodes deterministically.

``EpisodeBuilder`` is the mutable counterpart: it accumulates frames and
events while a window is open. On a trigger (app switch, idle, hard timer)
the tracker calls ``build()`` and receives a frozen ``Episode``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from jarvis.awareness.state import FrameSnapshot


@dataclass(frozen=True, slots=True)
class Episode:
    """Immutable, persist-ready episode data class.

    Fields correspond 1:1 to the ``awareness_episodes`` table from
    ``schema.sql`` (Slice A). ``tokens_in/out`` are 0 when the Verdichter
    was not invoked (empty episode or timeout).
    """
    started_at_ns: int
    ended_at_ns: int
    trigger_kind: str
    summary: str
    frame_count: int
    primary_app: str
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class _EventEntry:
    """Internal format for accumulated events.

    Exposed in ``EpisodeBuilder.events`` as a dict representation
    ``{kind, salience, payload, ts_ns}``.
    """
    kind: str
    salience: int
    payload: dict[str, Any]
    ts_ns: int


@dataclass
class _FrameEntry:
    """Internal format for accumulated frames (frame + score)."""
    frame: FrameSnapshot
    salience: int


@dataclass
class EpisodeBuilder:
    """Mutable builder. Accumulates frames and events while an episode is open.

    Lifecycle:

    1. ``__init__(started_at_ns)`` — mark the episode start.
    2. Repeatedly call ``add_frame(frame, salience)`` and/or
       ``add_event(kind, salience, payload)``.
    3. ``build(ended_at_ns=..., summary=..., …)`` → frozen ``Episode``.
       The builder persists after build() but can be reset
       (caller responsibility, not enforced here).

    The ``frames`` / ``events`` properties return **copies** so that
    the Verdichter cannot accidentally mutate the builder state.
    """
    started_at_ns: int
    _frames: list[_FrameEntry] = field(default_factory=list, init=False, repr=False)
    _events: list[_EventEntry] = field(default_factory=list, init=False, repr=False)

    def add_frame(self, frame: FrameSnapshot, salience: int) -> None:
        """Append a frame and its salience score to the buffer."""
        self._frames.append(_FrameEntry(frame=frame, salience=salience))

    def add_event(
        self, event_kind: str, salience: int, payload: dict[str, Any],
    ) -> None:
        """Buffer an event with salience, payload, and the current timestamp."""
        self._events.append(
            _EventEntry(
                kind=event_kind,
                salience=salience,
                payload=dict(payload),    # defensive copy
                ts_ns=time.time_ns(),
            ),
        )

    def detach_frames(self) -> list[FrameSnapshot]:
        """Atomic snapshot and reset of the internal frame buffer.

        Codex-BLOCKER B2-Fix (2026-05-11): the former ``frames`` property
        returned a copy but left the internal buffer in place. Combined with
        the B1-Fix (Verdichter call outside the lock), this created a race
        window — a concurrent ``add_frame`` call between snapshot creation
        and ``self._builder = None`` in StoryTracker could place a frame in
        a builder that was about to be discarded.

        Double-buffer pattern: we replace the internal list with a fresh
        empty list BEFORE returning. The caller (StoryTracker) holds the
        external ``_lock`` during the call, so the list the caller receives
        is fully decoupled from the builder, and any ``add_frame`` arriving
        after the lock is released lands in the fresh bucket (or, if the
        tracker sets ``self._builder = None``, in a new builder).

        IMPORTANT: This method is NOT thread-safe in isolation — atomicity
        comes from the external lock held by StoryTracker.
        """
        out = [entry.frame for entry in self._frames]
        self._frames = []
        return out

    def detach_events(self) -> list[dict[str, Any]]:
        """Atomic snapshot and reset of the internal event buffer.

        Same B2 guarantee as ``detach_frames`` — list swap before return,
        caller holds the external lock.
        """
        out = [
            {
                "kind": entry.kind,
                "salience": entry.salience,
                "payload": dict(entry.payload),
                "ts_ns": entry.ts_ns,
            }
            for entry in self._events
        ]
        self._events = []
        return out

    def build(
        self,
        *,
        ended_at_ns: int,
        summary: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> Episode:
        """Snapshot → frozen ``Episode``.

        ``trigger_kind`` is set by the caller (StoryTracker) — the builder
        does not know it. The default is empty; the tracker patches the field
        during the persist call alongside ``trigger_kind`` via ``replace()``.
        A sentinel is sufficient here — the actual trigger is added by the
        caller in the ``record_episode`` insert (Spec Slice A).
        """
        return Episode(
            started_at_ns=self.started_at_ns,
            ended_at_ns=ended_at_ns,
            trigger_kind="",    # set by the caller (StoryTracker)
            summary=summary,
            frame_count=len(self._frames),
            primary_app=self.primary_app,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    @property
    def frames(self) -> list[FrameSnapshot]:
        """Read-only copy of accumulated frames (in insertion order)."""
        return [entry.frame for entry in self._frames]

    @property
    def events(self) -> list[dict[str, Any]]:
        """Read-only copy of accumulated events.

        Format: ``{"kind": str, "salience": int, "payload": dict, "ts_ns": int}``.
        """
        return [
            {
                "kind": entry.kind,
                "salience": entry.salience,
                "payload": dict(entry.payload),
                "ts_ns": entry.ts_ns,
            }
            for entry in self._events
        ]

    @property
    def frame_count(self) -> int:
        """Number of accumulated frames."""
        return len(self._frames)

    @property
    def duration_ns(self) -> int:
        """Elapsed time since ``started_at_ns`` (live, not frozen)."""
        return time.time_ns() - self.started_at_ns

    @property
    def primary_app(self) -> str:
        """Process with the longest cumulative dwell time.

        Calculation: for each frame, the difference to the NEXT frame counts
        as dwell time. The last frame contributes 0 ns (we do not know how
        long it will remain active — the builder is live).

        Empty buffer: empty string. Exactly one frame: its
        ``active_process_name`` (even if dwell time is 0 —
        better than an empty string).

        Tie (multiple apps with equal time): the first one wins
        (insertion order via dict).
        """
        # Delegates to primary_app_from_snapshot — the same algorithm can be
        # called directly by StoryTracker on detach_frames() results without
        # needing to know the builder's internal format.
        return primary_app_from_snapshot(
            [entry.frame for entry in self._frames],
        )

    def is_empty(self) -> bool:
        """True if neither frames nor events have been accumulated."""
        return not self._frames and not self._events


def primary_app_from_snapshot(frames: list[FrameSnapshot]) -> str:
    """Standalone primary-app heuristic on a detached FrameSnapshot list.

    Identical semantics to ``EpisodeBuilder.primary_app`` — called by
    StoryTracker after ``builder.detach_frames()`` (at that point the builder
    is empty and the property would return "").
    """
    if not frames:
        return ""
    if len(frames) == 1:
        return frames[0].active_process_name

    dwell_by_app: dict[str, int] = {}
    for i in range(len(frames) - 1):
        dwell = frames[i + 1].timestamp_ns - frames[i].timestamp_ns
        if dwell < 0:    # defensive: out-of-order frames
            dwell = 0
        dwell_by_app[frames[i].active_process_name] = (
            dwell_by_app.get(frames[i].active_process_name, 0) + dwell
        )

    # Last frame: 0 ns contribution, but register it as a key so that
    # single-app episodes with only one process and dwell == 0 are handled.
    last_app = frames[-1].active_process_name
    dwell_by_app.setdefault(last_app, 0)

    # max() with tie-break via insertion order: dicts are insertion-ordered
    # from Python 3.7; max() returns the first element on equal values.
    return max(dwell_by_app, key=lambda app: dwell_by_app[app])
