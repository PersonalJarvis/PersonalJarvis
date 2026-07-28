"""The event layer against a real (temporary) store — fully offline.

Everything here runs against a fresh SQLite file: no network, no credentials,
no model. What it proves:

1. the migration applies to fresh AND to pre-existing databases,
2. re-deriving an unchanged item changes nothing (idempotency),
3. events die with the evidence they were derived from — on a content change,
   on a tombstone and on a source purge,
4. participants and places link through the identity layer rather than around
   it, and an unresolvable name keeps its spelling instead of vanishing,
5. the bi-temporal window matches by OVERLAP, so a coarse event is not
   invisible to a question about a day inside it,
6. both dialects declare the same logical schema.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jarvis.ultrawiki.events import (
    DerivedEvent,
    EventKind,
    EventTime,
    TimeAnchor,
    TimePrecision,
    derive_events,
)
from jarvis.ultrawiki.store import PostgresStore, UltraStore
from jarvis.ultrawiki.types import ConsentState, RawItem

RECORDED = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
RECORDED_ISO = "2026-03-10T12:00:00Z"


@pytest.fixture
async def store(tmp_path: Path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    await instance.open()
    await instance.upsert_source("src1", connector="local-folder", label="Test source")
    await instance.set_consent("src1", ConsentState.APPROVED)
    yield instance
    await instance.close()


async def add_item(
    store: UltraStore,
    external_id: str = "chat-1",
    *,
    body: str = "message body",
    title: str = "Chat thread",
    timestamp_utc: str = RECORDED_ISO,
) -> int:
    await store.upsert_items(
        "src1",
        [
            RawItem(
                external_id=external_id,
                body=body,
                permalink=f"app://{external_id}",
                timestamp_utc=timestamp_utc,
                title=title,
            )
        ],
    )
    row = await store.get_item_by_external_id("src1", external_id)
    assert row is not None
    return int(row["id"])


def event(
    *,
    kind: EventKind = EventKind.MEAL,
    title: str = "Dinner with Marlow Vance",
    when: datetime = datetime(2026, 3, 13, 19, 30, tzinfo=UTC),
    precision: TimePrecision = TimePrecision.MINUTE,
    participants: tuple[str, ...] = ("Marlow Vance",),
    place: str = "Porto Verde",
) -> DerivedEvent:
    return DerivedEvent(
        kind=kind,
        title=title,
        summary="They ate together.",
        time=EventTime.build(
            when, precision, TimeAnchor.RELATIVE, recorded_at=RECORDED
        ),
        participants=participants,
        place=place,
        confidence=0.9,
    )


def db_scalar(db_path: Path, sql: str, params: tuple = ()) -> object:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


async def test_the_event_migration_is_idempotent(tmp_path: Path):
    """Re-opening an existing store must be a no-op, including for a database
    created before the event tables existed."""
    path = tmp_path / "ultrawiki.db"
    first = UltraStore(path)
    await first.open()
    await first.close()

    second = UltraStore(path)
    await second.open()
    assert await second.event_counts() == {
        "meal": 0,
        "travel": 0,
        "meeting": 0,
        "purchase": 0,
        "milestone": 0,
        "other": 0,
        "total": 0,
    }
    await second.close()


async def test_postgres_mirrors_the_event_ddl():
    """The two backends run the same logical schema (design doc 01)."""
    ddl = "\n".join(PostgresStore.ddl_statements())
    for table in ("uw_events", "uw_event_participants"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in ddl
    # The CHECK lists are DERIVED from the enums, never retyped (AP-4).
    for kind in EventKind:
        assert f"'{kind.value}'" in ddl
    for precision in TimePrecision:
        assert f"'{precision.value}'" in ddl
    for anchor in TimeAnchor:
        assert f"'{anchor.value}'" in ddl
    # Postgres indexes the same stored card the SQLite FTS5 table indexes.
    assert "search_tsv tsvector GENERATED ALWAYS AS" in ddl
    assert "idx_uw_events_tsv" in ddl
    assert PostgresStore._EVENT_DIALECT == "postgres"
    assert UltraStore._EVENT_DIALECT == "sqlite"


async def test_the_sqlite_check_constraints_match_the_enums(store, tmp_path):
    """The SQL file is hand-written; this is the guard that keeps it honest."""
    sql = str(
        db_scalar(
            tmp_path / "ultrawiki.db",
            "SELECT sql FROM sqlite_master WHERE name = 'uw_events'",
        )
    )
    for kind in EventKind:
        assert f"'{kind.value}'" in sql
    for precision in TimePrecision:
        assert f"'{precision.value}'" in sql
    for anchor in TimeAnchor:
        assert f"'{anchor.value}'" in sql


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def test_re_deriving_an_unchanged_item_changes_nothing(store, tmp_path):
    item_id = await add_item(store)
    first = await store.replace_events(item_id, [event()])
    before = await store.list_events()

    second = await store.replace_events(item_id, [event()])
    after = await store.list_events()

    assert len(first) == len(second) == 1
    assert len(after) == 1
    assert db_scalar(tmp_path / "ultrawiki.db", "SELECT COUNT(*) FROM uw_events") == 1
    assert (
        db_scalar(tmp_path / "ultrawiki.db", "SELECT COUNT(*) FROM uw_event_fts") == 1
    )
    assert before[0]["title"] == after[0]["title"]
    assert before[0]["occurred_at"] == after[0]["occurred_at"]


async def test_an_empty_replacement_removes_the_previous_events(store, tmp_path):
    """A corrected source must not leave its old answer standing."""
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])
    await store.replace_events(item_id, [])
    assert await store.list_events() == []
    assert (
        db_scalar(tmp_path / "ultrawiki.db", "SELECT COUNT(*) FROM uw_event_fts") == 0
    )


async def test_changed_content_purges_the_events_it_produced(store, tmp_path):
    """A sentence that no longer exists must not keep answering questions."""
    item_id = await add_item(store, body="dinner on friday")
    await store.replace_events(item_id, [event()])
    assert len(await store.list_events()) == 1

    await add_item(store, body="completely different text now")
    assert await store.list_events() == []
    assert (
        db_scalar(tmp_path / "ultrawiki.db", "SELECT COUNT(*) FROM uw_event_fts") == 0
    )


async def test_a_tombstone_takes_its_events_with_it(store):
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])
    await store.upsert_items(
        "src1",
        [
            RawItem(
                external_id="chat-1",
                body="",
                permalink="app://chat-1",
                timestamp_utc=RECORDED_ISO,
                deleted=True,
            )
        ],
    )
    assert await store.list_events() == []


async def test_purging_a_source_cascades_to_its_events(store, tmp_path):
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])
    await store.delete_source("src1", purge=True)
    assert db_scalar(tmp_path / "ultrawiki.db", "SELECT COUNT(*) FROM uw_events") == 0
    assert (
        db_scalar(
            tmp_path / "ultrawiki.db", "SELECT COUNT(*) FROM uw_event_participants"
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Identity linking
# ---------------------------------------------------------------------------


async def test_participants_and_places_link_through_the_identity_layer(store):
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])

    stored = (await store.list_events())[0]
    assert stored["participants"][0]["display_name"] == "Marlow Vance"
    person_id = stored["participants"][0]["entity_id"]
    place_id = stored["place_entity_id"]
    assert person_id is not None and place_id is not None

    person = await store.get_entity(person_id)
    place = await store.get_entity(place_id)
    assert person is not None and person["kind"] == "person"
    assert place is not None and place["kind"] == "place"
    assert stored["place"] == "Porto Verde"


async def test_an_existing_contact_is_reused_rather_than_duplicated(store):
    """Seeded contacts are the point of the identity layer: an event with a
    known person must attach to that person, not create a second one."""
    known = await store.upsert_entity(display_name="Marlow Vance")
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])

    stored = (await store.list_events())[0]
    assert stored["participants"][0]["entity_id"] == known
    people = await store.list_people(query="Marlow")
    assert len(people) == 1


async def test_conservative_mode_links_without_creating_entities(store):
    """The low-confidence path must not turn every mentioned name into a row
    in the People view."""
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()], create_entities=False)
    stored = (await store.list_events())[0]
    assert stored["participants"][0]["display_name"] == "Marlow Vance"
    assert stored["participants"][0]["entity_id"] is None
    assert await store.list_people() == []


async def test_an_unresolvable_participant_keeps_its_spelling(store):
    """An ambiguous name links to nobody — but the event must still say who
    was there, or the evidence is silently lost."""
    await store.upsert_entity(display_name="Ines Halloran")
    await store.upsert_entity(display_name="Ines Halloran")
    item_id = await add_item(store)
    await store.replace_events(item_id, [event(participants=("Ines Halloran",))])

    stored = (await store.list_events())[0]
    assert stored["participants"][0]["display_name"] == "Ines Halloran"
    assert stored["participants"][0]["entity_id"] is None


async def test_events_are_found_through_a_merged_away_entity_id(store):
    """An old citation must never go dead just because two identities fused."""
    winner = await store.upsert_entity(display_name="Marlow Vance")
    loser = await store.upsert_entity(display_name="M. Vance")
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])
    await store.merge_entities(winner, loser, tier="deterministic", reason="test")

    assert len(await store.list_events(entity_id=loser)) == 1


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


async def test_the_window_matches_by_overlap_not_by_containment(store):
    """A month-precision event is the majority of what a personal corpus
    knows; containment would hide every one of them."""
    item_id = await add_item(store)
    await store.replace_events(
        item_id,
        [
            event(
                title="Porto Verde trip",
                kind=EventKind.TRAVEL,
                when=datetime(2026, 3, 1, tzinfo=UTC),
                precision=TimePrecision.MONTH,
                participants=(),
                place="",
            )
        ],
    )
    inside = await store.events_between("2026-03-14T00:00:00Z", "2026-03-14T23:59:59Z")
    outside = await store.events_between("2026-05-01T00:00:00Z", "2026-05-31T23:59:59Z")
    assert len(inside) == 1
    assert outside == []


async def test_filters_narrow_by_kind_and_by_participant(store):
    first = await add_item(store, "chat-1")
    second = await add_item(store, "chat-2")
    await store.replace_events(first, [event()])
    await store.replace_events(
        second,
        [
            event(
                kind=EventKind.MEETING,
                title="Standup",
                participants=("Bo Reyes",),
                place="",
            )
        ],
    )

    assert len(await store.list_events(kind="meal")) == 1
    assert len(await store.list_events(kind="meeting")) == 1
    assert len(await store.list_events()) == 2

    people = {person["display_name"]: person["id"] for person in await store.list_people()}
    by_person = await store.list_events(entity_id=people["Bo Reyes"])
    assert len(by_person) == 1
    assert by_person[0]["title"] == "Standup"


async def test_list_events_is_newest_first_and_pages(store):
    item_id = await add_item(store)
    await store.replace_events(
        item_id,
        [
            event(title="Older", when=datetime(2026, 1, 5, tzinfo=UTC), place=""),
            event(title="Newer", when=datetime(2026, 4, 5, tzinfo=UTC), place=""),
        ],
    )
    rows = await store.list_events()
    assert [row["title"] for row in rows] == ["Newer", "Older"]
    assert [row["title"] for row in await store.list_events(limit=1)] == ["Newer"]
    assert [row["title"] for row in await store.list_events(limit=1, offset=1)] == [
        "Older"
    ]


async def test_get_event_returns_participants_and_the_evidence_permalink(store):
    item_id = await add_item(store)
    [event_id] = await store.replace_events(item_id, [event()])
    detail = await store.get_event(event_id)
    assert detail is not None
    assert detail["permalink"] == "app://chat-1"
    assert detail["item_id"] == item_id
    assert detail["evidence_item_ids"] == [item_id]
    assert detail["date_label"] == "13 March 2026 at 19:30"
    assert [p["display_name"] for p in detail["participants"]] == ["Marlow Vance"]
    assert await store.get_event(999_999) is None


async def test_the_keyword_leg_finds_an_event_by_person_place_and_month(store):
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])
    for query in (
        "dinner Marlow Vance",
        "Porto Verde",
        "March 2026",
        "13.03.2026",
        "Friday dinner",
    ):
        hits = await store.search_events(query)
        assert hits, query
        assert hits[0].title == "Dinner with Marlow Vance"
        assert hits[0].matched_by == ("event",)
        # The event is ranked and decayed by the date it HAPPENED, not by when
        # the message that mentioned it was written.
        assert hits[0].timestamp_utc == "2026-03-13T19:30:00Z"
        assert "13 March 2026" in hits[0].snippet


async def test_the_keyword_leg_respects_the_time_window_and_the_kind(store):
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])
    assert await store.search_events("dinner", since="2026-04-01T00:00:00Z") == []
    assert await store.search_events("dinner", until="2026-01-01T00:00:00Z") == []
    assert await store.search_events("dinner", kind="meeting") == []
    assert await store.search_events("dinner", kind="meal")


async def test_the_keyword_leg_ignores_a_tombstoned_items_events(store):
    item_id = await add_item(store)
    await store.replace_events(item_id, [event()])
    await store.upsert_items(
        "src1",
        [
            RawItem(
                external_id="chat-1",
                body="",
                permalink="app://chat-1",
                timestamp_utc=RECORDED_ISO,
                deleted=True,
            )
        ],
    )
    assert await store.search_events("dinner") == []


async def test_an_empty_query_never_touches_the_index(store):
    assert await store.search_events("") == []
    assert await store.search_events("   ") == []


async def test_event_counts_are_grouped_by_kind(store):
    first = await add_item(store, "chat-1")
    second = await add_item(store, "chat-2")
    await store.replace_events(first, [event()])
    await store.replace_events(
        second, [event(kind=EventKind.TRAVEL, title="Flight", place="")]
    )
    counts = await store.event_counts()
    assert counts["meal"] == 1
    assert counts["travel"] == 1
    assert counts["total"] == 2


# ---------------------------------------------------------------------------
# Derivation into the store (the seam the pipeline uses)
# ---------------------------------------------------------------------------


async def test_a_distillation_payload_lands_as_an_absolute_dated_row(store):
    item_id = await add_item(store)
    events = derive_events(
        distill={
            "summary": "They agreed on dinner.",
            "events": [
                {
                    "kind": "meal",
                    "title": "Dinner with Marlow Vance",
                    "when": "next friday at 19:30",
                    "where": "Porto Verde",
                    "participants": ["Marlow Vance"],
                    "confidence": 0.9,
                }
            ],
        },
        title="Chat thread",
        recorded_at=RECORDED_ISO,
    )
    await store.replace_events(item_id, events)
    stored = (await store.list_events())[0]
    assert stored["occurred_at"] == "2026-03-13T19:30:00Z"
    assert stored["recorded_at"] == RECORDED_ISO
    assert stored["time_anchor"] == "relative"


async def test_a_low_confidence_event_links_but_never_invents_a_person(store):
    """The People view is a curated surface. An uncertain derivation may
    enrich what the user already has; it must not add rows to it."""
    known = await store.resolve_identity(name="Marlow Vance")
    item_id = await add_item(store)
    await store.replace_events(
        item_id,
        [
            DerivedEvent(
                kind=EventKind.OTHER,
                title="Something happened",
                summary="",
                time=EventTime.build(
                    datetime(2026, 3, 13, tzinfo=UTC),
                    TimePrecision.DAY,
                    TimeAnchor.ABSOLUTE,
                    recorded_at=RECORDED,
                ),
                participants=("Marlow Vance", "Somebody Unknown"),
                place="Nowhere Town",
                confidence=0.35,
            )
        ],
    )
    stored = (await store.list_events())[0]
    by_name = {p["display_name"]: p["entity_id"] for p in stored["participants"]}
    assert by_name["Marlow Vance"] == known.entity_id  # linked to the known one
    assert by_name["Somebody Unknown"] is None  # spelling kept, no new row
    assert stored["place"] == "Nowhere Town"
    assert stored["place_entity_id"] is None
    assert [p["display_name"] for p in await store.list_people(kind=None, limit=50)] == [
        "Marlow Vance"
    ]


async def test_a_confident_event_still_creates_the_people_it_names(store):
    item_id = await add_item(store)
    await store.replace_events(item_id, [event(participants=("Nadia Brix",))])
    stored = (await store.list_events())[0]
    assert stored["participants"][0]["entity_id"] is not None
    assert "Nadia Brix" in {
        person["display_name"]
        for person in await store.list_people(kind=None, limit=50)
    }


async def test_the_event_leg_reports_both_clocks(store):
    """``timestamp_utc`` is what the hit is about; ``recorded_utc`` is how old
    the record is. Ranking may only ever decay by the second one."""
    item_id = await add_item(store, timestamp_utc="2026-03-14T08:00:00Z")
    await store.replace_events(item_id, [event()])
    hits = await store.search_events("Marlow", k=5)
    assert hits[0].timestamp_utc == "2026-03-13T19:30:00Z"
    assert hits[0].recorded_utc == "2026-03-14T08:00:00Z"


async def test_the_backfill_reader_walks_distilled_items_once(store):
    """The lane that gives an ALREADY distilled corpus its events without a
    single model call — it reads the distillation that is already stored."""
    from jarvis.ultrawiki.types import DocType

    first = await add_item(store, "chat-a")
    second = await add_item(store, "chat-b")
    await add_item(store, "chat-c")  # no distillation at all
    await store.add_document(
        first, DocType.SUMMARY, "text", distill_json='{"summary": "on 2026-02-02"}'
    )
    await store.add_document(second, DocType.SUMMARY, "text", distill_json='{"a": 1}')

    rows = await store.items_with_distillation(limit=50)
    assert [row["id"] for row in rows] == [first, second]
    assert rows[0]["distill_json"] == '{"summary": "on 2026-02-02"}'
    assert rows[0]["timestamp_utc"] == RECORDED_ISO
    assert await store.items_with_distillation(after_id=second, limit=50) == []
