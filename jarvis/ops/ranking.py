"""Deterministic ranking of WorkLedger items with the person's OpsPriority marks.

Pure functions only: no I/O, no clock read (``today``/``now_ms`` are passed
in), no model call. Every point an item gets is listed in its ``reasons``, so
the order can always be explained.

**Lanes first, score second.** An item's lane comes from its ledger status and
is never overridden by a score — a parked or failed item cannot outrank work
that can actually move:

- ``active``   — running, queued, scheduled
- ``blocked``  — waiting_capacity, waiting, paused (cannot move on its own)
- ``failed``   — failed (needs a look, but is not active work)
- ``finished`` — done, cancelled, skipped

Items that need a decision from the person (``needs_attention``, e.g. a
mission in ``WAITING_CAPACITY``) are additionally listed under ``needs_you``;
they stay in their ``blocked`` lane.

**Score inside a lane** (higher first):

- focus today: +1000
- explicit priority: urgent +300, high +200, normal 0, low -100;
  no mark = 0 ("no_priority") — the ranking never invents a priority
- active lane only — due: overdue +30, due within 24 h +20;
  status: running +10, queued +5
- preference sources (later: the person's context file): each signal is
  clamped to ±50 and their sum to ±50, so they can order items of equal
  priority but never beat an explicit priority or focus

Ties: earlier due first, then most recently updated, then source + id.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from jarvis.ops.ledger import WorkItem
from jarvis.ops.priority import PriorityMark

LANES: tuple[str, ...] = ("active", "blocked", "failed", "finished")
LANE_OF_STATUS: Mapping[str, str] = {
    "running": "active",
    "queued": "active",
    "scheduled": "active",
    "waiting_capacity": "blocked",
    "waiting": "blocked",
    "paused": "blocked",
    "failed": "failed",
    "done": "finished",
    "cancelled": "finished",
    "skipped": "finished",
}

FOCUS_POINTS = 1000
PRIORITY_POINTS: Mapping[str, int] = {"urgent": 300, "high": 200, "normal": 0, "low": -100}
OVERDUE_POINTS = 30
DUE_24H_POINTS = 20
STATUS_POINTS: Mapping[str, int] = {"running": 10, "queued": 5}
PREFERENCE_CAP = 50
_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class PreferenceSignal:
    """One preference source's opinion on one item: a small bounded nudge."""

    delta: int
    reason: str


class PreferenceSource(Protocol):
    """Extra, person-specific ordering hints (e.g. a future context file).

    ``signal`` must be pure and deterministic: same item, same answer. No
    source is wired in yet; the ranking works the same without any.
    """

    name: str

    def signal(self, item: WorkItem) -> PreferenceSignal | None: ...


@dataclass(frozen=True, slots=True)
class RankedItem:
    item: WorkItem
    lane: str
    score: int
    reasons: tuple[str, ...]
    priority: str | None
    focus_today: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.item.to_dict(),
            "lane": self.lane,
            "score": self.score,
            "reasons": list(self.reasons),
            "priority": self.priority,
            "focus_today": self.focus_today,
        }


@dataclass(frozen=True, slots=True)
class Agenda:
    today: date
    lanes: Mapping[str, tuple[RankedItem, ...]]
    needs_you: tuple[RankedItem, ...]
    focus: tuple[RankedItem, ...]
    #: Marks whose item was not in the snapshot (finished long ago, deleted,
    #: or its source was not readable this time).
    unmatched_marks: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "today": self.today.isoformat(),
            "lanes": {lane: [r.to_dict() for r in self.lanes[lane]] for lane in LANES},
            "needs_you": [r.to_dict() for r in self.needs_you],
            "focus": [r.to_dict() for r in self.focus],
            "unmatched_marks": self.unmatched_marks,
        }


def lane_of(item: WorkItem) -> str:
    # An unknown status is treated as blocked: never promoted to active work.
    return LANE_OF_STATUS.get(item.status, "blocked")


def score_item(
    item: WorkItem,
    mark: PriorityMark | None,
    *,
    today: date,
    now_ms: int,
    preferences: Sequence[PreferenceSource] = (),
) -> RankedItem:
    lane = lane_of(item)
    score = 0
    reasons: list[str] = []
    focus = mark is not None and mark.focus_on(today)
    if focus:
        score += FOCUS_POINTS
        reasons.append("focus_today")
    priority = mark.priority if mark is not None else None
    if priority is None:
        reasons.append("no_priority")
    else:
        score += PRIORITY_POINTS[priority]
        reasons.append(f"priority:{priority}")
    if lane == "active":
        if item.due_ms is not None:
            if item.due_ms < now_ms:
                score += OVERDUE_POINTS
                reasons.append("overdue")
            elif item.due_ms - now_ms <= _DAY_MS:
                score += DUE_24H_POINTS
                reasons.append("due_within_24h")
        bonus = STATUS_POINTS.get(item.status, 0)
        if bonus:
            score += bonus
            reasons.append(f"status:{item.status}")
    else:
        reasons.append(f"{lane}:{item.status}")
    nudge = 0
    for source in preferences:
        signal = source.signal(item)
        if signal is None or signal.delta == 0:
            continue
        delta = max(-PREFERENCE_CAP, min(PREFERENCE_CAP, int(signal.delta)))
        nudge += delta
        reasons.append(f"preference:{source.name}:{signal.reason}")
    score += max(-PREFERENCE_CAP, min(PREFERENCE_CAP, nudge))
    return RankedItem(item, lane, score, tuple(reasons), priority, focus)


def _sort_key(ranked: RankedItem) -> tuple[int, int, int, str, str]:
    item = ranked.item
    due = item.due_ms if item.due_ms is not None else 2**62
    recent = item.updated_ms or item.created_ms or 0
    return (-ranked.score, due, -recent, item.source, item.id)


def build_agenda(
    items: Iterable[WorkItem],
    marks: Iterable[PriorityMark],
    *,
    today: date,
    now_ms: int,
    preferences: Sequence[PreferenceSource] = (),
) -> Agenda:
    by_key = {(m.source, m.item_id): m for m in marks}
    seen: set[tuple[str, str]] = set()
    lanes: dict[str, list[RankedItem]] = {lane: [] for lane in LANES}
    for item in items:
        key = (item.source, item.id)
        seen.add(key)
        ranked = score_item(
            item, by_key.get(key), today=today, now_ms=now_ms, preferences=preferences
        )
        lanes[ranked.lane].append(ranked)
    ordered = {lane: tuple(sorted(lanes[lane], key=_sort_key)) for lane in LANES}
    flat = [r for lane in LANES for r in ordered[lane]]
    return Agenda(
        today=today,
        lanes=ordered,
        needs_you=tuple(r for r in flat if r.item.needs_attention),
        focus=tuple(r for r in flat if r.focus_today),
        unmatched_marks=sum(1 for key in by_key if key not in seen),
    )


__all__ = [
    "LANES",
    "LANE_OF_STATUS",
    "Agenda",
    "PreferenceSignal",
    "PreferenceSource",
    "RankedItem",
    "build_agenda",
    "lane_of",
    "score_item",
]
