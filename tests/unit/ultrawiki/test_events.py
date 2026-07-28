"""Event derivation logic — pure, offline, no store and no model.

The centrepiece is the time anchoring: design doc 01 forbids storing a
relative expression as text, so every path through this module has to end in
an absolute instant or in nothing at all. The tests below pin the three
outcomes apart (absolute / relative / recorded), pin the refusals (a bare
number is not a clock, a slash date is not parsed, an item without a
timestamp yields no event), and pin the prompt and the resolver to the same
closed vocabulary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jarvis.ultrawiki.distill import _RELATIVE_TOKENS, PROMPT_VERSION
from jarvis.ultrawiki.events import (
    EVENT_VERSION,
    MAX_EVENTS_PER_ITEM,
    RELATIVE_VOCABULARY,
    DerivedEvent,
    EventKind,
    EventTime,
    TimeAnchor,
    TimePrecision,
    coerce_kind,
    derive_events,
    format_occurred,
    iso_utc,
    parse_absolute,
    parse_instant,
    resolve_time,
    scan_absolute_dates,
    window_end,
)

#: A Tuesday, so "last friday" and "next friday" land on different days and a
#: test cannot pass by accident on a week boundary.
ANCHOR = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
ANCHOR_ISO = "2026-03-10T12:00:00Z"


def test_the_anchor_fixture_is_a_tuesday():
    """Every weekday expectation below depends on it."""
    assert ANCHOR.weekday() == 1


# ---------------------------------------------------------------------------
# Instant parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-14T19:30:00Z", "2026-03-14T19:30:00Z"),
        ("2026-03-14T19:30:00+02:00", "2026-03-14T17:30:00Z"),
        ("2026-03-14T19:30:00-05:00", "2026-03-15T00:30:00Z"),
        ("2026-03-14 19:30", "2026-03-14T19:30:00Z"),
        ("2026-03-14", "2026-03-14T00:00:00Z"),
    ],
)
def test_parse_instant_accepts_the_shapes_connectors_actually_emit(raw, expected):
    parsed = parse_instant(raw)
    assert parsed is not None
    assert iso_utc(parsed) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "next friday", "14.03.2026", "2026-13-40", "not a date"]
)
def test_parse_instant_refuses_everything_else(raw):
    """An unparseable timestamp must never quietly become "now"."""
    assert parse_instant(raw) is None


# ---------------------------------------------------------------------------
# Absolute expressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "instant", "precision"),
    [
        ("2026-03-14T19:30", "2026-03-14T19:30:00Z", TimePrecision.MINUTE),
        ("2026-03-14", "2026-03-14T00:00:00Z", TimePrecision.DAY),
        ("2026-03-14 at 19:30", "2026-03-14T19:30:00Z", TimePrecision.MINUTE),
        ("2026-03", "2026-03-01T00:00:00Z", TimePrecision.MONTH),
        ("2026", "2026-01-01T00:00:00Z", TimePrecision.YEAR),
        ("14.03.2026", "2026-03-14T00:00:00Z", TimePrecision.DAY),
        ("14 March 2026", "2026-03-14T00:00:00Z", TimePrecision.DAY),
        ("March 14, 2026", "2026-03-14T00:00:00Z", TimePrecision.DAY),
        ("14 March 2026 at 7pm", "2026-03-14T19:00:00Z", TimePrecision.MINUTE),
        ("14 March 2026 evening", "2026-03-14T19:00:00Z", TimePrecision.MINUTE),
    ],
)
def test_parse_absolute_covers_iso_dotted_and_written_dates(raw, instant, precision):
    parsed = parse_absolute(raw)
    assert parsed is not None
    moment, level = parsed
    assert iso_utc(moment) == instant
    assert level is precision


def test_slash_dates_are_deliberately_not_parsed():
    """03/04/2026 is two different days on two continents.

    A personal memory that guesses which one is worse than one that stays
    quiet: the expression falls through to the relative resolver, finds
    nothing, and the event is anchored on the item's own timestamp with
    ``TimeAnchor.RECORDED`` instead of on an invented day.
    """
    assert parse_absolute("03/04/2026") is None
    resolved = resolve_time("03/04/2026", recorded_at=ANCHOR)
    assert resolved.anchor is TimeAnchor.RECORDED


def test_a_written_date_without_a_year_is_refused():
    """Filling the year in from the anchor would be a guess, and a guessed
    year silently files a memory under the wrong twelve months."""
    assert parse_absolute("14 March") is None


# ---------------------------------------------------------------------------
# Relative expressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected", "precision"),
    [
        ("today", "2026-03-10T00:00:00Z", TimePrecision.DAY),
        ("yesterday", "2026-03-09T00:00:00Z", TimePrecision.DAY),
        ("tomorrow", "2026-03-11T00:00:00Z", TimePrecision.DAY),
        ("next friday", "2026-03-13T00:00:00Z", TimePrecision.DAY),
        ("last friday", "2026-03-06T00:00:00Z", TimePrecision.DAY),
        ("this friday", "2026-03-13T00:00:00Z", TimePrecision.DAY),
        ("last monday", "2026-03-09T00:00:00Z", TimePrecision.DAY),
        ("3 days ago", "2026-03-07T00:00:00Z", TimePrecision.DAY),
        ("in 3 days", "2026-03-13T00:00:00Z", TimePrecision.DAY),
        ("2 weeks ago", "2026-02-24T00:00:00Z", TimePrecision.WEEK),
        ("in 2 months", "2026-05-10T00:00:00Z", TimePrecision.MONTH),
        ("last week", "2026-03-02T00:00:00Z", TimePrecision.WEEK),
        ("next week", "2026-03-16T00:00:00Z", TimePrecision.WEEK),
        ("last month", "2026-02-01T00:00:00Z", TimePrecision.MONTH),
        ("next month", "2026-04-01T00:00:00Z", TimePrecision.MONTH),
        ("last year", "2025-01-01T00:00:00Z", TimePrecision.YEAR),
        ("next year", "2027-01-01T00:00:00Z", TimePrecision.YEAR),
    ],
)
def test_relative_expressions_resolve_against_the_items_own_timestamp(
    raw, expected, precision
):
    resolved = resolve_time(raw, recorded_at=ANCHOR)
    assert resolved.occurred_at == expected
    assert resolved.precision is precision
    assert resolved.anchor is TimeAnchor.RELATIVE
    assert resolved.recorded_at == ANCHOR_ISO


def test_a_relative_day_may_carry_a_clock_time():
    resolved = resolve_time("next friday at 19:30", recorded_at=ANCHOR)
    assert resolved.occurred_at == "2026-03-13T19:30:00Z"
    assert resolved.precision is TimePrecision.MINUTE


def test_a_bare_count_is_never_read_as_a_clock_time():
    """"3 days ago" must not become 03:00 — stating an invented hour as a fact
    is exactly the failure absolute anchoring exists to prevent."""
    resolved = resolve_time("3 days ago", recorded_at=ANCHOR)
    assert resolved.occurred_at == "2026-03-07T00:00:00Z"
    assert resolved.precision is TimePrecision.DAY


def test_a_daypart_word_sharpens_a_relative_day():
    resolved = resolve_time("tomorrow morning", recorded_at=ANCHOR)
    assert resolved.occurred_at == "2026-03-11T09:00:00Z"


def test_an_unknown_expression_falls_back_to_the_recorded_moment():
    resolved = resolve_time("whenever we last got around to it", recorded_at=ANCHOR)
    assert resolved.occurred_at == ANCHOR_ISO
    assert resolved.anchor is TimeAnchor.RECORDED
    assert resolved.precision is TimePrecision.MINUTE


def test_an_absolute_expression_beats_a_relative_one():
    resolved = resolve_time("2026-03-14", recorded_at=ANCHOR)
    assert resolved.anchor is TimeAnchor.ABSOLUTE
    assert resolved.occurred_at == "2026-03-14T00:00:00Z"


def test_every_token_of_the_closed_vocabulary_actually_resolves():
    """A token the prompt may emit and the resolver does not know would
    silently degrade every event of that shape to ``RECORDED``."""
    for token in RELATIVE_VOCABULARY:
        phrase = token.replace("<weekday>", "friday").replace("<n>", "3")
        resolved = resolve_time(phrase, recorded_at=ANCHOR)
        assert resolved.anchor is TimeAnchor.RELATIVE, phrase


def test_the_prompt_and_the_resolver_share_one_vocabulary():
    """Drift here is invisible in production: the model keeps emitting a token
    nothing resolves, and every affected event quietly loses its date."""
    assert set(_RELATIVE_TOKENS.split("|")) == set(RELATIVE_VOCABULARY)
    assert PROMPT_VERSION >= 2  # the version that introduced the events array


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("precision", "expected_end"),
    [
        (TimePrecision.MINUTE, "2026-03-10T12:00:59Z"),
        (TimePrecision.HOUR, "2026-03-10T12:59:59Z"),
        (TimePrecision.DAY, "2026-03-10T23:59:59Z"),
        (TimePrecision.WEEK, "2026-03-16T23:59:59Z"),
        (TimePrecision.MONTH, "2026-03-31T23:59:59Z"),
        (TimePrecision.YEAR, "2026-12-31T23:59:59Z"),
    ],
)
def test_precision_defines_the_interval_the_event_occupies(precision, expected_end):
    """"In March" is one event covering 31 days, not one at midnight on the
    1st — a range query that treats it as a point silently misses it."""
    assert iso_utc(window_end(ANCHOR, precision)) == expected_end


def test_an_explicit_end_wins_over_the_precision_window():
    resolved = resolve_time("2026-03-14", recorded_at=ANCHOR, raw_end="2026-03-18")
    assert resolved.occurred_at == "2026-03-14T00:00:00Z"
    assert resolved.occurred_end == "2026-03-18T23:59:59Z"


def test_an_end_before_the_start_is_ignored_rather_than_stored_backwards():
    resolved = resolve_time("2026-03-14", recorded_at=ANCHOR, raw_end="2026-03-01")
    assert resolved.occurred_end == "2026-03-14T23:59:59Z"


@pytest.mark.parametrize(
    ("precision", "label"),
    [
        (TimePrecision.MINUTE, "10 March 2026 at 12:00"),
        (TimePrecision.HOUR, "10 March 2026 around 12:00"),
        (TimePrecision.DAY, "10 March 2026"),
        (TimePrecision.WEEK, "week of 10 March 2026"),
        (TimePrecision.MONTH, "March 2026"),
        (TimePrecision.YEAR, "2026"),
    ],
)
def test_one_date_is_written_one_way_everywhere(precision, label):
    assert format_occurred(ANCHOR_ISO, precision) == label


# ---------------------------------------------------------------------------
# Derivation — the structured path
# ---------------------------------------------------------------------------


def structured(**overrides):
    entry = {
        "kind": "meal",
        "title": "Dinner with Marlow Vance",
        "when": "next friday at 19:30",
        "where": "Porto Verde",
        "participants": ["Marlow Vance"],
        "confidence": 0.9,
    }
    entry.update(overrides)
    return {"summary": "They agreed on dinner.", "events": [entry]}


def test_a_structured_event_keeps_its_kind_place_and_participants():
    events = derive_events(
        distill=structured(), title="Chat thread", recorded_at=ANCHOR_ISO
    )
    assert len(events) == 1
    event = events[0]
    assert event.kind is EventKind.MEAL
    assert event.title == "Dinner with Marlow Vance"
    assert event.place == "Porto Verde"
    assert event.participants == ("Marlow Vance",)
    assert event.time.occurred_at == "2026-03-13T19:30:00Z"
    assert event.time.anchor is TimeAnchor.RELATIVE
    assert event.time.recorded_at == ANCHOR_ISO
    assert event.extraction_version == EVENT_VERSION


def test_an_unknown_kind_degrades_to_other_instead_of_being_dropped():
    events = derive_events(distill=structured(kind="wedding"), recorded_at=ANCHOR_ISO)
    assert events[0].kind is EventKind.OTHER
    assert coerce_kind(None) is EventKind.OTHER


def test_an_item_without_a_parseable_timestamp_yields_nothing():
    """No anchor, no event: an episodic row floating in an unknown year is
    worse than no row at all."""
    assert derive_events(distill=structured(), recorded_at="") == []
    assert derive_events(distill=structured(), recorded_at="whenever") == []


def test_an_empty_entry_is_dropped_and_a_missing_array_yields_nothing():
    assert derive_events(distill={"events": [{}]}, recorded_at=ANCHOR_ISO) == []
    assert derive_events(distill={"events": "nonsense"}, recorded_at=ANCHOR_ISO) == []
    assert derive_events(distill=None, recorded_at=ANCHOR_ISO) == []
    assert derive_events(distill={}, recorded_at=ANCHOR_ISO) == []


def test_the_per_item_event_cap_holds():
    payload = {
        "events": [
            {"title": f"Meeting {index}", "when": f"2026-03-{index + 1:02d}"}
            for index in range(20)
        ]
    }
    events = derive_events(distill=payload, recorded_at=ANCHOR_ISO)
    assert len(events) == MAX_EVENTS_PER_ITEM


def test_identical_entries_collapse_to_one_event():
    payload = {"events": [structured()["events"][0], structured()["events"][0]]}
    assert len(derive_events(distill=payload, recorded_at=ANCHOR_ISO)) == 1


def test_the_participant_list_is_capped_and_deduplicated():
    payload = structured(participants=["Ines Halloran", "ines halloran", "Bo Reyes"])
    events = derive_events(distill=payload, recorded_at=ANCHOR_ISO)
    assert events[0].participants == ("Ines Halloran", "Bo Reyes")


def test_confidence_is_clamped_rather_than_trusted():
    assert derive_events(distill=structured(confidence=7), recorded_at=ANCHOR_ISO)[
        0
    ].confidence == 1.0
    assert derive_events(distill=structured(confidence="junk"), recorded_at=ANCHOR_ISO)[
        0
    ].confidence == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Derivation — the legacy path (a distillation from before the events array)
# ---------------------------------------------------------------------------


def test_a_pre_v2_distillation_still_yields_an_event_when_it_states_a_date():
    """The whole point of the legacy path: a corpus distilled under prompt
    version 1 gains episodic rows WITHOUT being re-distilled — it reads only
    text that is already stored."""
    legacy = {
        "question": "When did the Porto Verde trip start?",
        "summary": "The trip began on 2026-02-02 and ran for a week.",
        "resolution": "",
        "entities": ["Marlow Vance", "Porto Verde"],
    }
    events = derive_events(distill=legacy, recorded_at=ANCHOR_ISO)
    assert len(events) == 1
    assert events[0].time.occurred_at == "2026-02-02T00:00:00Z"
    assert events[0].time.anchor is TimeAnchor.ABSOLUTE
    assert events[0].kind is EventKind.OTHER  # no kind can be known without a lexicon
    assert events[0].confidence < 0.5  # honest: a date and no story


def test_the_legacy_path_never_turns_mentioned_entities_into_participants():
    """``entities`` is one bag of people, places, organizations AND systems —
    reading it as a guest list is how a city and a database end up in the
    People view. A legacy event carries a date and no names."""
    legacy = {
        "question": "When did the Porto Verde trip start?",
        "summary": "The trip began on 2026-02-02 and ran for a week.",
        "entities": ["Marlow Vance", "Porto Verde", "Postgres", "Acme GmbH"],
    }
    events = derive_events(distill=legacy, recorded_at=ANCHOR_ISO)
    assert len(events) == 1
    assert events[0].participants == ()
    assert events[0].place == ""


def test_a_pre_v2_distillation_without_a_date_yields_nothing():
    """Otherwise every one of a quarter-million items would become an event."""
    legacy = {
        "question": "What did they decide about the deployment?",
        "summary": "They agreed to ship on the usual cadence.",
        "entities": ["Bo Reyes"],
    }
    assert derive_events(distill=legacy, recorded_at=ANCHOR_ISO) == []


def test_the_legacy_path_is_capped_at_two_dates():
    legacy = {
        "summary": "2026-01-01 then 2026-02-02 then 2026-03-03 then 2026-04-04.",
    }
    assert len(derive_events(distill=legacy, recorded_at=ANCHOR_ISO)) == 2


def test_scan_absolute_dates_keeps_source_order_and_drops_duplicates():
    found = scan_absolute_dates("first 2026-02-02, again 2026-02-02, then 14.03.2026")
    assert [iso_utc(moment) for moment, _ in found] == [
        "2026-02-02T00:00:00Z",
        "2026-03-14T00:00:00Z",
    ]


# ---------------------------------------------------------------------------
# The stored card + dedupe identity
# ---------------------------------------------------------------------------


def make_event(**overrides) -> DerivedEvent:
    values = {
        "kind": EventKind.MEAL,
        "title": "Dinner with Marlow Vance",
        "summary": "They ate together.",
        "time": EventTime.build(
            datetime(2026, 3, 13, 19, 30, tzinfo=UTC),
            TimePrecision.MINUTE,
            TimeAnchor.RELATIVE,
            recorded_at=ANCHOR,
        ),
        "participants": ("Marlow Vance",),
        "place": "Porto Verde",
    }
    values.update(overrides)
    return DerivedEvent(**values)


def test_the_search_card_carries_the_date_in_several_written_forms():
    """A user asks "in March", "on the 13th" and "2026" for the same event."""
    card = make_event().search_text()
    for fragment in (
        "Dinner with Marlow Vance",
        "13 March 2026 at 19:30",
        "2026-03-13",
        "13.03.2026",
        "Friday",
        "March",
        "2026",
        "Porto Verde",
    ):
        assert fragment in card, fragment


def test_the_dedupe_key_is_stable_across_re_derivations():
    assert make_event().dedupe_key == make_event().dedupe_key


@pytest.mark.parametrize(
    "change",
    [
        {"title": "Lunch with Marlow Vance"},
        {"place": "Halloran Bay"},
        {"participants": ("Bo Reyes",)},
        {"kind": EventKind.MEETING},
    ],
)
def test_a_different_event_gets_a_different_dedupe_key(change):
    assert make_event().dedupe_key != make_event(**change).dedupe_key


def test_to_dict_exposes_both_clocks_and_the_honest_anchor():
    payload = make_event().to_dict()
    assert payload["occurred_at"] == "2026-03-13T19:30:00Z"
    assert payload["recorded_at"] == ANCHOR_ISO
    assert payload["time_anchor"] == "relative"
    assert payload["date_label"] == "13 March 2026 at 19:30"
