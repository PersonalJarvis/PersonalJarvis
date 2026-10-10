"""Calendar day: upcoming, cancelled, moved and short-notice — never invented.

Only facts the calendar API states are used: ``status``, ``original_start``
(a moved instance of a series) and ``updated`` (the change time).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis.ops.briefing import BriefingSection, render_text
from jarvis.ops.calendar_day import SHORT_NOTICE_HOURS, classify_day

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 7, 7, 30, tzinfo=TZ)
TODAY = NOW.date()


def _ev(event_id: str, start: str | None, end: str | None = None, **kw: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"id": event_id, "summary": event_id.title(), "start": start}
    if end is not None:
        event["end"] = end
    event.update(kw)
    return event


def _classify(*events: dict[str, Any]) -> tuple[tuple[dict[str, Any], ...], ...]:
    return classify_day(TODAY, events, tz=TZ, now=NOW)


def _by_id(entries: tuple[dict[str, Any], ...]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in entries}


# --- Cancelled ------------------------------------------------------------------


def test_a_cancelled_event_is_never_upcoming() -> None:
    upcoming, cancelled = _classify(
        _ev("call", "2026-10-07T12:00:00+02:00", "2026-10-07T13:00:00+02:00", status="cancelled"),
        _ev("lunch", "2026-10-07T12:30:00+02:00", "2026-10-07T13:30:00+02:00"),
    )
    assert [e["id"] for e in upcoming] == ["lunch"]
    assert [(e["id"], e["state"]) for e in cancelled] == [("call", "cancelled")]


def test_a_cancelled_series_instance_is_placed_by_its_original_slot() -> None:
    _upcoming, cancelled = _classify(
        _ev("weekly", None, status="cancelled", original_start="2026-10-07T15:00:00+02:00"),
    )
    assert [(e["id"], e["time"]) for e in cancelled] == [("weekly", "15:00")]


def test_a_cancelled_event_without_any_time_is_left_out() -> None:
    upcoming, cancelled = _classify(_ev("ghost", None, status="cancelled"))
    assert upcoming == () and cancelled == ()


def test_cancellations_of_other_days_are_not_listed() -> None:
    _upcoming, cancelled = _classify(
        _ev("tomorrow", "2026-10-08T09:00:00+02:00", status="cancelled"),
    )
    assert cancelled == ()


# --- Moved ----------------------------------------------------------------------


def test_a_moved_series_instance_shows_its_old_and_new_time() -> None:
    upcoming, _ = _classify(
        _ev(
            "standup",
            "2026-10-07T10:00:00+02:00",
            "2026-10-07T10:15:00+02:00",
            original_start="2026-10-07T09:00:00+02:00",
            updated="2026-10-07T05:00:00Z",
        ),
    )
    entry = upcoming[0]
    assert entry["time"] == "10:00"
    assert entry["moved_from"] == {
        "start": "2026-10-07T09:00:00+02:00",
        "time": "09:00",
        "day": "2026-10-07",
    }
    assert entry["short_notice"] is True  # changed 2 h before the original slot


def test_a_move_from_another_day_names_that_day() -> None:
    upcoming, _ = _classify(
        _ev(
            "review",
            "2026-10-07T16:00:00+02:00",
            original_start="2026-10-06T16:00:00+02:00",
            updated="2026-09-30T10:00:00Z",
        ),
    )
    assert upcoming[0]["moved_from"]["day"] == "2026-10-06"
    assert upcoming[0]["short_notice"] is False  # changed a week ahead


def test_a_series_instance_in_its_own_slot_is_not_moved() -> None:
    upcoming, _ = _classify(
        _ev(
            "standup",
            "2026-10-07T09:00:00+02:00",
            original_start="2026-10-07T07:00:00Z",  # the same instant, another zone
            updated="2026-10-07T05:00:00Z",
        ),
    )
    assert upcoming[0]["moved_from"] is None
    assert upcoming[0]["short_notice"] is False and upcoming[0]["changed_at"] is None


def test_a_single_event_has_no_history_so_nothing_is_invented() -> None:
    """Google states no earlier time for a single event: even a fresh update
    yields no 'moved from' and no short-notice flag."""
    upcoming, _ = _classify(
        _ev("dentist", "2026-10-07T11:00:00+02:00", updated="2026-10-07T07:00:00Z"),
    )
    entry = upcoming[0]
    assert entry["moved_from"] is None
    assert entry["short_notice"] is False
    assert entry["changed_at"] is None


# --- Short notice ---------------------------------------------------------------


def test_short_notice_needs_a_known_change_time() -> None:
    _u, cancelled = _classify(
        _ev(
            "late", "2026-10-07T12:00:00+02:00", status="cancelled", updated="2026-10-07T06:00:00Z"
        ),
        _ev(
            "early", "2026-10-07T13:00:00+02:00", status="cancelled", updated="2026-10-01T06:00:00Z"
        ),
        _ev("unknown", "2026-10-07T14:00:00+02:00", status="cancelled"),
    )
    flags = {e["id"]: e["short_notice"] for e in cancelled}
    assert flags == {"late": True, "early": False, "unknown": False}
    assert _by_id(cancelled)["unknown"]["changed_at"] is None


def test_the_short_notice_window_is_measured_from_the_original_slot() -> None:
    original = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
    just_inside = (original - timedelta(hours=SHORT_NOTICE_HOURS)).isoformat()
    just_outside = (original - timedelta(hours=SHORT_NOTICE_HOURS, minutes=1)).isoformat()
    upcoming, _ = _classify(
        _ev(
            "a",
            "2026-10-07T18:00:00+02:00",
            original_start=original.isoformat(),
            updated=just_inside,
        ),
        _ev(
            "b",
            "2026-10-07T19:00:00+02:00",
            original_start=original.isoformat(),
            updated=just_outside,
        ),
    )
    assert {e["id"]: e["short_notice"] for e in upcoming} == {"a": True, "b": False}


# --- Upcoming -------------------------------------------------------------------


def test_events_that_are_over_are_not_upcoming_but_all_day_stays() -> None:
    upcoming, _ = _classify(
        _ev("early", "2026-10-07T06:00:00+02:00", "2026-10-07T07:00:00+02:00"),
        _ev("now", "2026-10-07T07:00:00+02:00", "2026-10-07T08:00:00+02:00"),
        _ev("holiday", "2026-10-07", "2026-10-08"),
    )
    assert [e["id"] for e in upcoming] == ["holiday", "now"]


def test_duplicates_are_listed_once_and_tentative_is_kept() -> None:
    twice = _ev("sync", "2026-10-07T10:00:00+02:00", status="tentative")
    upcoming, _ = _classify(twice, dict(twice))
    assert [(e["id"], e["state"]) for e in upcoming] == [("sync", "tentative")]


# --- Rendering ------------------------------------------------------------------


def _render(language: str, *events: dict[str, Any]) -> str:
    upcoming, cancelled = _classify(*events)
    sections = (
        BriefingSection("calendar", len(upcoming), upcoming),
        BriefingSection("calendar_cancelled", len(cancelled), cancelled),
    )
    return render_text(sections, day=TODAY, language=language, address=None)


MOVED_SHORT = _ev(
    "standup",
    "2026-10-07T10:00:00+02:00",
    original_start="2026-10-07T09:00:00+02:00",
    updated="2026-10-07T05:00:00Z",
)
CANCELLED_SHORT = _ev(
    "call", "2026-10-07T12:00:00+02:00", status="cancelled", updated="2026-10-07T05:00:00Z"
)


def test_the_text_marks_moves_and_short_notice_changes() -> None:
    text = _render("en", MOVED_SHORT, CANCELLED_SHORT, _ev("sync", "2026-10-07T11:00:00+02:00"))
    assert "- (!) 10:00 Standup (moved from 09:00; short-notice change)" in text
    assert "- 11:00 Sync\n" in text
    assert "Cancelled appointments (1):\n- (!) 12:00 Call (short-notice change)" in text


def test_the_text_marks_changes_in_german() -> None:
    text = _render("de", MOVED_SHORT, CANCELLED_SHORT)
    assert "Anstehende Termine:" in text  # i18n-allow: asserts German output
    assert "verschoben von 09:00; kurzfristig geändert" in text  # i18n-allow
    assert "Abgesagte Termine (1):" in text  # i18n-allow


def test_no_cancelled_heading_without_cancellations() -> None:
    text = _render("en", _ev("sync", "2026-10-07T11:00:00+02:00"))
    assert "Cancelled appointments" not in text


# --- Three separate groups in the briefing ----------------------------------------


def _briefing_sections(*events: dict[str, Any]) -> tuple[Any, ...]:
    from jarvis.ops.briefing import build_sections
    from jarvis.ops.calendar_day import CalendarDay
    from jarvis.ops.ranking import build_agenda

    upcoming, cancelled = _classify(*events)
    agenda = build_agenda([], [], today=TODAY, now_ms=int(NOW.timestamp() * 1000))
    return build_sections(agenda, CalendarDay("ok", upcoming, cancelled), now=NOW)


def _ids_of(sections: tuple[Any, ...], key: str) -> list[str]:
    return [e["id"] for s in sections if s.key == key for e in s.items]


PLAIN = _ev("sync", "2026-10-07T11:00:00+02:00")


def test_each_appointment_is_in_exactly_one_group() -> None:
    sections = _briefing_sections(PLAIN, MOVED_SHORT, CANCELLED_SHORT)
    assert _ids_of(sections, "calendar") == ["sync"]
    assert _ids_of(sections, "calendar_moved") == ["standup"]
    assert _ids_of(sections, "calendar_cancelled") == ["call"]


def test_the_text_has_three_separate_headings_in_order() -> None:
    from jarvis.ops.briefing import render_text

    text = render_text(
        _briefing_sections(PLAIN, MOVED_SHORT, CANCELLED_SHORT),
        day=TODAY,
        language="en",
        address=None,
    )
    upcoming = text.index("Upcoming appointments:")
    moved = text.index("Moved appointments (1):")
    cancelled = text.index("Cancelled appointments (1):")
    assert upcoming < moved < cancelled
    assert "- (!) 10:00 Standup (moved from 09:00; short-notice change)" in text[moved:cancelled]
    assert "Standup" not in text[upcoming:moved]


def test_only_moved_appointments_today_have_no_empty_upcoming_heading() -> None:
    from jarvis.ops.briefing import render_text

    text = render_text(_briefing_sections(MOVED_SHORT), day=TODAY, language="de", address=None)
    assert "Anstehende Termine" not in text  # i18n-allow: asserts German output
    assert "Verschobene Termine (1):" in text  # i18n-allow
    nothing = render_text(_briefing_sections(), day=TODAY, language="en", address=None)
    assert "No upcoming appointments today." in nothing


async def test_a_move_is_notified_once_and_a_new_move_again(tmp_path: Any) -> None:
    from jarvis.ops.briefing import Briefing, render_text
    from jarvis.ops.notify import (
        NotifyStore,
        OwnerNotifier,
        SimulatedTelegramTransport,
        notifications_from_briefing,
    )

    def _briefing(event: dict[str, Any]) -> Briefing:
        sections = _briefing_sections(event)
        return Briefing(
            TODAY, "en", sections, render_text(sections, day=TODAY, language="en", address=None)
        )

    store = NotifyStore(tmp_path / "ops.sqlite")
    await store.save_settings(enabled=True, kinds=["appointment_moved"])
    transport = SimulatedTelegramTransport()
    notifier = OwnerNotifier(store, transport)
    for _ in range(3):  # the briefing runs again and again the same day
        await notifier.deliver(notifications_from_briefing(_briefing(MOVED_SHORT)))
    moved_again = dict(MOVED_SHORT, start="2026-10-07T11:30:00+02:00")
    await notifier.deliver(notifications_from_briefing(_briefing(moved_again)))
    assert [m.split(":")[0] for m in transport.sent] == ["Appointment moved"] * 2
    assert "11:30" in transport.sent[1] and "10:00" in transport.sent[0]
