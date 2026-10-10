"""OpsPriority ranking: deterministic, explainable, lane-safe.

A blocked or failed item never outranks active work, an item without a mark
has no invented priority, and a preference source can only nudge items of
equal priority. Pure functions: no I/O, no clock, no model.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import date

from jarvis.ops.ledger import WORK_STATUSES, WorkItem
from jarvis.ops.priority import PriorityMark
from jarvis.ops.ranking import (
    LANE_OF_STATUS,
    LANES,
    PreferenceSignal,
    build_agenda,
    lane_of,
    score_item,
)

TODAY = date(2026, 10, 7)
NOW = 1_800_000_000_000
HOUR = 3_600_000


def _item(item_id: str, status: str = "queued", **kw: object) -> WorkItem:
    defaults: dict[str, object] = {
        "source": "task",
        "id": item_id,
        "title": item_id,
        "status": status,
        "source_state": status,
        "updated_ms": 1_000,
    }
    defaults.update(kw)
    return WorkItem(**defaults)  # type: ignore[arg-type]


def _mark(item_id: str, priority: str | None = None, focus: date | None = None) -> PriorityMark:
    return PriorityMark(
        "task", item_id, priority=priority, focus_date=focus.isoformat() if focus else None
    )


def _ids(ranked: object) -> list[str]:
    return [r.item.id for r in ranked]  # type: ignore[attr-defined]


# --- Lanes ----------------------------------------------------------------------


def test_every_ledger_status_has_a_lane() -> None:
    assert set(LANE_OF_STATUS) == set(WORK_STATUSES)
    assert set(LANE_OF_STATUS.values()) == set(LANES)


def test_a_parked_mission_never_ranks_as_active_work() -> None:
    parked = _item(
        "parked",
        "waiting_capacity",
        source="mission",
        needs_attention=True,
        attention_reason="capacity_decision",
    )
    plain = _item("plain", "queued")
    marks = [PriorityMark("mission", "parked", "urgent", TODAY.isoformat())]

    agenda = build_agenda([parked, plain], marks, today=TODAY, now_ms=NOW)

    assert _ids(agenda.lanes["active"]) == ["plain"]
    assert _ids(agenda.lanes["blocked"]) == ["parked"]
    assert _ids(agenda.needs_you) == ["parked"]  # it needs a decision, not work
    assert _ids(agenda.focus) == ["parked"]  # the focus mark stays visible
    assert "blocked:waiting_capacity" in agenda.lanes["blocked"][0].reasons


def test_waiting_paused_failed_and_finished_items_stay_out_of_the_active_lane() -> None:
    items = [
        _item("w", "waiting"),
        _item("p", "paused"),
        _item("f", "failed"),
        _item("d", "done"),
        _item("c", "cancelled"),
        _item("s", "skipped"),
        _item("r", "running"),
    ]
    marks = [_mark(i.id, "urgent", TODAY) for i in items if i.id != "r"]
    agenda = build_agenda(items, marks, today=TODAY, now_ms=NOW)
    assert _ids(agenda.lanes["active"]) == ["r"]
    assert sorted(_ids(agenda.lanes["blocked"])) == ["p", "w"]
    assert _ids(agenda.lanes["failed"]) == ["f"]
    assert sorted(_ids(agenda.lanes["finished"])) == ["c", "d", "s"]


def test_an_unknown_status_is_never_promoted_to_active() -> None:
    assert lane_of(_item("x", "from-the-future")) == "blocked"


# --- Score ----------------------------------------------------------------------


def test_no_mark_means_no_invented_priority() -> None:
    ranked = score_item(_item("a", "scheduled"), None, today=TODAY, now_ms=NOW)
    assert (ranked.score, ranked.priority, ranked.focus_today) == (0, None, False)
    assert ranked.reasons == ("no_priority",)


def test_explicit_priorities_order_and_unmarked_is_neutral() -> None:
    items = [_item(n, "scheduled") for n in ("low", "none", "normal", "high", "urgent")]
    marks = [_mark(p, p) for p in ("low", "normal", "high", "urgent")]
    agenda = build_agenda(items, marks, today=TODAY, now_ms=NOW)
    order = _ids(agenda.lanes["active"])
    assert order[:2] == ["urgent", "high"]
    assert set(order[2:4]) == {"none", "normal"}  # both neutral, no invented rank
    assert order[4] == "low"


def test_focus_today_beats_urgent_and_lapses_the_next_day() -> None:
    focus = _item("focus", "scheduled")
    urgent = _item("urgent", "running")
    marks = [_mark("focus", None, TODAY), _mark("urgent", "urgent")]
    agenda = build_agenda([urgent, focus], marks, today=TODAY, now_ms=NOW)
    assert _ids(agenda.lanes["active"]) == ["focus", "urgent"]

    tomorrow = date(2026, 10, 8)
    later = build_agenda([urgent, focus], marks, today=tomorrow, now_ms=NOW)
    assert _ids(later.lanes["active"]) == ["urgent", "focus"]
    assert later.focus == ()


def test_due_counts_only_for_active_work() -> None:
    overdue = score_item(_item("o", "scheduled", due_ms=NOW - HOUR), None, today=TODAY, now_ms=NOW)
    soon = score_item(_item("s", "scheduled", due_ms=NOW + HOUR), None, today=TODAY, now_ms=NOW)
    later = score_item(
        _item("l", "scheduled", due_ms=NOW + 72 * HOUR), None, today=TODAY, now_ms=NOW
    )
    blocked = score_item(_item("b", "paused", due_ms=NOW - HOUR), None, today=TODAY, now_ms=NOW)
    assert "overdue" in overdue.reasons and overdue.score == 30
    assert "due_within_24h" in soon.reasons and soon.score == 20
    assert later.score == 0
    assert blocked.score == 0 and "overdue" not in blocked.reasons


def test_due_and_status_never_beat_an_explicit_priority() -> None:
    hurry = _item("hurry", "running", due_ms=NOW - HOUR)
    high = _item("high", "scheduled")
    agenda = build_agenda([hurry, high], [_mark("high", "high")], today=TODAY, now_ms=NOW)
    assert _ids(agenda.lanes["active"]) == ["high", "hurry"]


# --- Preferences (the future context file) --------------------------------------


@dataclass
class Prefers:
    name: str
    wanted: str
    delta: int

    def signal(self, item: WorkItem) -> PreferenceSignal | None:
        return PreferenceSignal(self.delta, "match") if item.id == self.wanted else None


def test_a_preference_is_capped_and_cannot_beat_an_explicit_priority() -> None:
    liked = _item("liked", "scheduled")
    high = _item("high", "scheduled")
    loud = Prefers("context", "liked", 10_000)
    agenda = build_agenda(
        [liked, high], [_mark("high", "high")], today=TODAY, now_ms=NOW, preferences=[loud]
    )
    assert _ids(agenda.lanes["active"]) == ["high", "liked"]
    top = agenda.lanes["active"][1]
    assert top.score == 50 and "preference:context:match" in top.reasons


def test_a_preference_orders_items_of_equal_priority() -> None:
    a, b = _item("a", "scheduled"), _item("b", "scheduled")
    agenda = build_agenda([a, b], [], today=TODAY, now_ms=NOW, preferences=[Prefers("x", "b", 5)])
    assert _ids(agenda.lanes["active"]) == ["b", "a"]


def test_several_preferences_share_one_cap() -> None:
    item = _item("a", "scheduled")
    sources = [Prefers("one", "a", 40), Prefers("two", "a", 40)]
    assert score_item(item, None, today=TODAY, now_ms=NOW, preferences=sources).score == 50


# --- Determinism ----------------------------------------------------------------


def test_the_order_is_deterministic_whatever_the_input_order() -> None:
    items = [
        _item(
            f"t{n}",
            ("queued", "scheduled", "running", "paused", "failed")[n % 5],
            due_ms=NOW + (n % 3) * HOUR,
            updated_ms=n % 4,
        )
        for n in range(30)
    ]
    marks = [
        _mark(f"t{n}", ("urgent", "high", "normal", "low", None)[n % 5]) for n in range(0, 30, 2)
    ]
    first = build_agenda(items, marks, today=TODAY, now_ms=NOW).to_dict()
    rng = random.Random(7)  # noqa: S311 - a fixed shuffle order for a test, not crypto
    for _ in range(5):
        shuffled = items[:]
        rng.shuffle(shuffled)
        again = build_agenda(shuffled, list(reversed(marks)), today=TODAY, now_ms=NOW)
        assert again.to_dict() == first


def test_ties_break_by_due_then_recency_then_id() -> None:
    items = [
        _item("b", "scheduled", due_ms=NOW + 30 * HOUR, updated_ms=5),
        _item("a", "scheduled", due_ms=NOW + 30 * HOUR, updated_ms=5),
        _item("newer", "scheduled", due_ms=NOW + 30 * HOUR, updated_ms=9),
        _item("sooner", "scheduled", due_ms=NOW + 26 * HOUR, updated_ms=1),
    ]
    agenda = build_agenda(items, [], today=TODAY, now_ms=NOW)
    assert _ids(agenda.lanes["active"]) == ["sooner", "newer", "a", "b"]


def test_marks_without_an_item_are_counted_not_applied() -> None:
    agenda = build_agenda(
        [_item("a")], [_mark("a", "high"), _mark("gone", "urgent")], today=TODAY, now_ms=NOW
    )
    assert agenda.unmatched_marks == 1
    assert _ids(agenda.lanes["active"]) == ["a"]


def test_ranking_never_changes_the_items() -> None:
    item = _item("a", "waiting_capacity", needs_attention=True)
    agenda = build_agenda([item], [_mark("a", "urgent", TODAY)], today=TODAY, now_ms=NOW)
    assert agenda.lanes["blocked"][0].item == replace(item)
