"""UltraWiki projection unit tests — the readable wiki model, fully offline.

The store is a log: thousands of small items whose titles repeat by the
hundred. The projection is what makes it readable — it folds the entities and
questions the distiller already extracted into browsable pages and a graph.

Covers: label normalization (NFC, whitespace, punctuation, length bounds),
case-folded identity with most-frequent-spelling display, moment titles and
their fallbacks, mention counts and first/last-seen bounds, co-occurrence
neighbours, tombstone and non-distilled exclusion, malformed JSON tolerance,
the graph threshold, and cache invalidation. Real UltraStore, no network.
"""

from __future__ import annotations

import json

import pytest

from jarvis.ultrawiki import projection as projection_mod
from jarvis.ultrawiki.projection import (
    PROJECTION_CACHE_TTL_S,
    build_projection,
    get_projection,
    normalize_entity_label,
)
from jarvis.ultrawiki.store import UltraStore
from jarvis.ultrawiki.types import DocType, ItemState, RawItem


@pytest.fixture
async def store(tmp_path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    await instance.open()
    yield instance
    await instance.close()


async def seed_source(store: UltraStore, source_id: str = "src1") -> str:
    await store.upsert_source(source_id, connector="local-folder", label="Test source")
    return source_id


async def seed_distilled(
    store: UltraStore,
    external_id: str,
    *,
    source_id: str = "src1",
    question: str = "",
    summary: str = "",
    resolution: str = "",
    entities: list[str] | None = None,
    timestamp_utc: str = "2026-01-02T10:00:00Z",
    raw_distill_json: str | None = None,
) -> int:
    """One captured item plus its distilled summary document."""
    await store.upsert_items(
        source_id,
        [
            RawItem(
                external_id=external_id,
                body=summary or question or external_id,
                permalink=f"app://{external_id}",
                timestamp_utc=timestamp_utc,
                title=f"Conversation on {timestamp_utc[:10]}",
            )
        ],
    )
    item = await store.get_item_by_external_id(source_id, external_id)
    assert item is not None
    payload = (
        raw_distill_json
        if raw_distill_json is not None
        else json.dumps(
            {
                "question": question,
                "summary": summary,
                "resolution": resolution,
                "entities": entities or [],
                "refs": [],
            }
        )
    )
    await store.add_document(
        item["id"],
        DocType.SUMMARY,
        summary or question,
        distill_json=payload,
        distill_version=1,
    )
    await store.mark_stage_done(item["id"], ItemState.DISTILLED)
    return int(item["id"])


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------


def test_normalize_collapses_whitespace_and_strips_edge_punctuation():
    assert normalize_entity_label("  Bora   Bora,  ") == "Bora Bora"


def test_normalize_folds_decomposed_unicode_to_composed_form():
    # macOS hands text and filenames back DECOMPOSED (NFD). Without NFC the
    # same name is two entities AND two files that collide on re-export.
    # Spelled as escapes on purpose: written as literal characters the
    # source file gets normalized and this assertion passes vacuously.
    decomposed = "Cafe\u0301 Central"  # e + COMBINING ACUTE ACCENT
    composed = "Caf\u00e9 Central"  # LATIN SMALL LETTER E WITH ACUTE
    assert decomposed != composed
    assert normalize_entity_label(decomposed) == composed


def test_normalize_rejects_labels_that_are_too_short_or_too_long():
    assert normalize_entity_label("a") is None
    assert normalize_entity_label("x" * 81) is None


def test_normalize_rejects_blank_and_punctuation_only_labels():
    assert normalize_entity_label("   ") is None
    assert normalize_entity_label("...") is None


# ---------------------------------------------------------------------------
# Entity folding
# ---------------------------------------------------------------------------


async def test_spellings_differing_only_in_case_become_one_entity(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="Q1", entities=["OpenClaw"])
    await seed_distilled(store, "b", question="Q2", entities=["openclaw"])
    await seed_distilled(store, "c", question="Q3", entities=["OpenClaw"])

    projection = await build_projection(store)

    assert [e.key for e in projection.entities] == ["openclaw"]
    entity = projection.entities[0]
    assert entity.mentions == 3
    # Display uses the spelling the corpus actually favours, not the first one
    # seen and not the case-folded key.
    assert entity.label == "OpenClaw"


async def test_entity_records_mention_count_and_time_span(store):
    await seed_source(store)
    await seed_distilled(
        store,
        "a",
        question="Q1",
        entities=["Tahiti"],
        timestamp_utc="2026-01-05T10:00:00Z",
    )
    await seed_distilled(
        store,
        "b",
        question="Q2",
        entities=["Tahiti"],
        timestamp_utc="2026-03-09T10:00:00Z",
    )

    projection = await build_projection(store)
    entity = projection.entity_by_key["tahiti"]

    assert entity.mentions == 2
    assert entity.first_seen == "2026-01-05T10:00:00Z"
    assert entity.last_seen == "2026-03-09T10:00:00Z"


async def test_entities_sort_by_mentions_descending(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="Q1", entities=["Rare"])
    for i in range(3):
        await seed_distilled(store, f"b{i}", question="Q", entities=["Common"])

    projection = await build_projection(store)

    assert [e.label for e in projection.entities] == ["Common", "Rare"]


async def test_entities_sharing_a_moment_become_neighbours(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="Trip", entities=["Bora Bora", "Tahiti"])
    await seed_distilled(store, "b", question="Trip again", entities=["Bora Bora", "Tahiti"])
    await seed_distilled(store, "c", question="Elsewhere", entities=["Berlin"])

    projection = await build_projection(store)

    bora = projection.entity_by_key["bora bora"]
    assert bora.neighbors == (("tahiti", 2),)
    assert projection.entity_by_key["berlin"].neighbors == ()


# ---------------------------------------------------------------------------
# Moments
# ---------------------------------------------------------------------------


async def test_moment_title_is_the_distilled_question(store):
    await seed_source(store)
    await seed_distilled(
        store,
        "a",
        question="How do I get to Bora Bora?",
        summary="Discussed flight routes.",
        resolution="Fly via Tahiti.",
        entities=["Bora Bora"],
    )

    projection = await build_projection(store)
    moment = projection.moments[0]

    # NOT the item title, which repeats by the hundred in a real corpus.
    assert moment.title == "How do I get to Bora Bora?"
    assert moment.summary == "Discussed flight routes."
    assert moment.resolution == "Fly via Tahiti."
    assert moment.entity_keys == ("bora bora",)
    assert moment.permalink == "app://a"


async def test_moment_without_a_question_falls_back_to_its_summary(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="", summary="A note about caching.")

    projection = await build_projection(store)

    assert projection.moments[0].title == "A note about caching."


async def test_moment_without_question_or_summary_falls_back_to_item_title(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="", summary="")

    projection = await build_projection(store)

    assert projection.moments[0].title == "Conversation on 2026-01-02"


async def test_moments_are_newest_first(store):
    await seed_source(store)
    await seed_distilled(store, "old", question="Older", timestamp_utc="2026-01-01T10:00:00Z")
    await seed_distilled(store, "new", question="Newer", timestamp_utc="2026-06-01T10:00:00Z")

    projection = await build_projection(store)

    assert [m.title for m in projection.moments] == ["Newer", "Older"]


async def test_moment_carries_its_source_label(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="Q")

    projection = await build_projection(store)

    assert projection.moments[0].source_label == "Test source"


# ---------------------------------------------------------------------------
# Exclusions and tolerance
# ---------------------------------------------------------------------------


async def test_items_without_a_distillation_produce_no_moment(store):
    source_id = await seed_source(store)
    await store.upsert_items(
        source_id,
        [
            RawItem(
                external_id="raw-only",
                body="Some raw text",
                permalink="app://raw-only",
                timestamp_utc="2026-01-02T10:00:00Z",
            )
        ],
    )
    item = await store.get_item_by_external_id(source_id, "raw-only")
    assert item is not None
    await store.add_document(item["id"], DocType.RAW, "Some raw text")

    projection = await build_projection(store)

    assert projection.moments == ()
    assert projection.entities == ()


async def test_tombstoned_items_leave_the_projection(store):
    source_id = await seed_source(store)
    await seed_distilled(store, "gone", question="Q", entities=["Ghost"])
    await store.reconcile_deletes(source_id, set())

    projection = await build_projection(store)

    assert projection.moments == ()
    assert projection.entities == ()


async def test_malformed_distillation_json_is_skipped_not_fatal(store):
    await seed_source(store)
    await seed_distilled(store, "bad", raw_distill_json="{not json")
    await seed_distilled(store, "good", question="Fine", entities=["Berlin"])

    projection = await build_projection(store)

    assert [m.title for m in projection.moments] == ["Fine"]
    assert [e.key for e in projection.entities] == ["berlin"]


async def test_non_string_entity_values_are_ignored(store):
    await seed_source(store)
    await seed_distilled(
        store,
        "a",
        question="Q",
        raw_distill_json=json.dumps(
            {
                "question": "Q",
                "summary": "",
                "resolution": "",
                "entities": ["Berlin", 42, None, {"name": "x"}],
                "refs": [],
            }
        ),
    )

    projection = await build_projection(store)

    assert [e.key for e in projection.entities] == ["berlin"]


async def test_empty_corpus_projects_to_an_empty_model(store):
    await seed_source(store)

    projection = await build_projection(store)

    assert projection.entities == ()
    assert projection.moments == ()
    assert projection.graph(min_mentions=1) == ([], [])


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


async def test_graph_threshold_hides_entities_below_the_mention_floor(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="Q1", entities=["Common", "Rare"])
    await seed_distilled(store, "b", question="Q2", entities=["Common"])

    projection = await build_projection(store)

    nodes, _ = projection.graph(min_mentions=2)
    assert [n["key"] for n in nodes] == ["common"]

    nodes, _ = projection.graph(min_mentions=1)
    assert {n["key"] for n in nodes} == {"common", "rare"}


async def test_graph_drops_edges_whose_endpoint_was_filtered_out(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="Q1", entities=["Common", "Rare"])
    await seed_distilled(store, "b", question="Q2", entities=["Common"])

    projection = await build_projection(store)
    _, edges = projection.graph(min_mentions=2)

    # An edge pointing at a hidden node would render as a dangling line.
    assert edges == []


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


async def test_repeated_calls_reuse_the_cached_projection(store):
    await seed_source(store)
    await seed_distilled(store, "a", question="Q")

    first = await get_projection(store)
    second = await get_projection(store)

    assert first is second


async def test_new_distillation_refreshes_after_the_short_cache_window(
    store, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr(projection_mod, "_monotonic", lambda: clock[0])
    await seed_source(store)
    await seed_distilled(store, "a", question="Q")
    first = await get_projection(store)

    await seed_distilled(store, "b", question="Q2")
    still_cached = await get_projection(store)
    assert still_cached is first

    clock[0] += PROJECTION_CACHE_TTL_S + 0.1
    second = await get_projection(store)

    assert second is not first
    assert len(second.moments) == 2
