"""UltraStore unit tests — SQLite backend over tmp_path DBs, fully offline.

Covers: schema idempotency, source CRUD + consent, idempotent item upserts,
tombstones + reconcile, FTS lockstep with the state machine, the embedding
pin, reset_vectors, the real sqlite-vec round-trip (when the host has the
extension) AND the honest degraded path, plus the Postgres driver's missing-
dependency message. No network, no credentials, no models.
"""

from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace

import pytest

import jarvis.ultrawiki.store as store_mod
from jarvis.ultrawiki.store import (
    MAX_ATTEMPTS,
    META_EMBED_DIM,
    META_EMBED_MODEL,
    PostgresStore,
    UltraStore,
    UltraStoreError,
    resolve_ultrawiki_db_path,
)
from jarvis.ultrawiki.types import ConsentState, DocType, ItemState, RawItem

HAS_SQLITE_VEC = importlib.util.find_spec("sqlite_vec") is not None


def make_item(
    index: int,
    *,
    body: str | None = None,
    title: str | None = None,
    deleted: bool = False,
) -> RawItem:
    hours, rem = divmod(index, 3600)
    minutes, seconds = divmod(rem, 60)
    return RawItem(
        external_id=f"ext-{index:04d}",
        body=body if body is not None else f"body text number{index} alpha",
        permalink=f"app://item/{index}",
        timestamp_utc=f"2026-01-01T{hours % 24:02d}:{minutes:02d}:{seconds:02d}Z",
        title=title if title is not None else f"Item {index}",
        deleted=deleted,
    )


@pytest.fixture
async def store(tmp_path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    yield instance
    await instance.close()


async def add_source(
    store: UltraStore, source_id: str = "src1", areas: list[str] | None = None
) -> str:
    await store.upsert_source(
        source_id, connector="local-folder", label="Test source", areas=areas
    )
    await store.set_consent(source_id, ConsentState.APPROVED)
    return source_id


async def index_keyword(store: UltraStore, claimed: dict) -> None:
    await store.mark_stage_done(
        claimed["id"],
        ItemState.KEYWORD_INDEXED,
        fts_title=claimed["title"],
        fts_body=claimed["body_raw"],
    )


# ---------------------------------------------------------------------------
# Path resolution + schema idempotency
# ---------------------------------------------------------------------------


def test_path_resolution_is_absolute_and_cwd_independent(tmp_path):
    relative = resolve_ultrawiki_db_path("data")
    assert relative.is_absolute()
    assert relative.name == "ultrawiki.db"
    absolute = resolve_ultrawiki_db_path(tmp_path)
    assert absolute == (tmp_path / "ultrawiki.db").resolve()
    default = resolve_ultrawiki_db_path(None)
    assert default.is_absolute()


async def test_double_open_is_idempotent(tmp_path):
    db_path = tmp_path / "ultrawiki.db"
    first = UltraStore(db_path)
    await first.open()
    await first.open()  # second open on the same instance is a no-op
    await add_source(first)
    await first.upsert_items("src1", [make_item(0)])
    await first.close()

    # A brand-new instance re-applies the idempotent schema and sees the data.
    second = UltraStore(db_path)
    await second.open()
    counts = await second.counts()
    assert counts.total == 1
    await second.close()


# ---------------------------------------------------------------------------
# Sources & consent
# ---------------------------------------------------------------------------


async def test_source_crud_and_consent_transitions(store):
    await store.upsert_source(
        "src1", connector="local-folder", label="Notes", areas=["work"]
    )
    source = await store.get_source("src1")
    assert source is not None
    assert source["consent"] == ConsentState.PENDING.value
    assert source["enabled"] is True
    assert source["areas"] == ["work"]

    await store.set_consent("src1", ConsentState.APPROVED)
    assert (await store.get_source("src1"))["consent"] == "approved"
    await store.set_consent("src1", "revoked")  # plain string accepted
    assert (await store.get_source("src1"))["consent"] == "revoked"
    with pytest.raises(ValueError):
        await store.set_consent("src1", "maybe")

    await store.set_enabled("src1", False)
    assert (await store.get_source("src1"))["enabled"] is False

    # A config upsert must never reset user-granted consent/enablement.
    await store.upsert_source("src1", connector="local-folder", label="Renamed")
    source = await store.get_source("src1")
    assert source["label"] == "Renamed"
    assert source["consent"] == "revoked"
    assert source["enabled"] is False
    assert source["areas"] == ["work"]  # None areas keeps existing

    listed = await store.list_sources()
    assert len(listed) == 1
    assert listed[0]["counts"].total == 0
    assert listed[0]["sync_state"] is None


async def test_delete_source_purge_semantics(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(i) for i in range(3)])
    await store.set_sync_state("src1", cursor="abc")

    # purge=False disconnects but keeps captured data.
    await store.delete_source("src1", purge=False)
    source = await store.get_source("src1")
    assert source is not None
    assert source["consent"] == "revoked"
    assert source["enabled"] is False
    assert (await store.counts_for_source("src1")).total == 3

    # purge=True removes the source and every derived row.
    await store.delete_source("src1", purge=True)
    assert await store.get_source("src1") is None
    assert (await store.counts()).total == 0
    assert await store.get_sync_state("src1") is None


# ---------------------------------------------------------------------------
# Item upserts, tombstones, reconcile
# ---------------------------------------------------------------------------


async def test_item_upsert_idempotency_counts(store):
    await add_source(store)
    items = [make_item(i) for i in range(5)]
    first = await store.upsert_items("src1", items)
    assert (first.new, first.changed, first.unchanged) == (5, 0, 0)

    second = await store.upsert_items("src1", items)
    assert (second.new, second.changed, second.unchanged) == (0, 0, 5)

    edited = [make_item(0, body="edited body zeta"), *items[1:]]
    third = await store.upsert_items("src1", edited)
    assert (third.new, third.changed, third.unchanged) == (0, 1, 4)
    changed_item = await store.get_item_by_external_id("src1", "ext-0000")
    assert changed_item["state"] == ItemState.CAPTURED.value
    assert changed_item["body_raw"] == "edited body zeta"

    with pytest.raises(UltraStoreError):
        await store.upsert_items("ghost-source", items)


async def test_tombstones_and_reconcile_deletes(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(i) for i in range(4)])
    for claimed in await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10):
        await index_keyword(store, claimed)

    # Explicit tombstone via RawItem.deleted.
    result = await store.upsert_items("src1", [make_item(0, deleted=True)])
    assert result.tombstoned == 1
    assert (await store.counts()).total == 3
    tombstoned = await store.get_item_by_external_id("src1", "ext-0000")
    assert tombstoned["deleted_at"] is not None
    assert await store.keyword_search("number0") == []

    # A FULL backfill that no longer yields ext-0001 tombstones it.
    survivors = {"ext-0002", "ext-0003"}
    removed = await store.reconcile_deletes("src1", survivors)
    assert removed == 1
    assert (await store.counts()).total == 2
    assert await store.keyword_search("number1") == []
    assert len(await store.keyword_search("number2")) == 1

    # Resurrection: the tombstoned item coming back is treated as changed.
    revived = await store.upsert_items("src1", [make_item(0)])
    assert revived.changed == 1
    assert (await store.counts()).total == 3


# ---------------------------------------------------------------------------
# FTS lockstep with the state machine
# ---------------------------------------------------------------------------


async def test_fts_lockstep_with_keyword_stage(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(0)])

    # Captured but not yet keyword-indexed: not findable.
    assert await store.keyword_search("number0") == []

    claimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10)
    assert len(claimed) == 1
    await index_keyword(store, claimed[0])

    hits = await store.keyword_search("number0")
    assert len(hits) == 1
    hit = hits[0]
    assert hit.item_id == claimed[0]["id"]
    assert hit.source_id == "src1"
    assert hit.permalink == "app://item/0"
    assert hit.matched_by == ("keyword",)
    assert 0.0 <= hit.score <= 1.0

    # Changed content resets the state AND removes the stale FTS row in the
    # same batch; the new body is findable only after re-indexing.
    await store.upsert_items("src1", [make_item(0, body="completely new gamma")])
    assert await store.keyword_search("number0") == []
    assert await store.keyword_search("gamma") == []
    reclaimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10)
    assert len(reclaimed) == 1
    await index_keyword(store, reclaimed[0])
    assert await store.keyword_search("number0") == []
    assert len(await store.keyword_search("gamma")) == 1


# ---------------------------------------------------------------------------
# Claim ordering, retry backoff, dead-letter
# ---------------------------------------------------------------------------


async def test_claim_batch_orders_newest_first_and_honors_retry(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(i) for i in range(3)])

    claimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=2)
    assert [item["external_id"] for item in claimed] == ["ext-0002", "ext-0001"]

    victim = claimed[0]
    await store.mark_retry(victim["id"], "stage blew up", now="2026-02-01T00:00:00Z")
    after = await store.get_item(victim["id"])
    assert after["attempt_count"] == 1
    assert after["state"] == ItemState.CAPTURED.value  # keeps last good state
    assert after["last_error"] == "stage blew up"

    # Not eligible before the 60 s backoff elapses; eligible afterwards.
    at_failure = [
        item["external_id"]
        for item in await store.claim_batch(
            ItemState.KEYWORD_INDEXED, limit=10, now="2026-02-01T00:00:30Z"
        )
    ]
    assert victim["external_id"] not in at_failure
    after_backoff = [
        item["external_id"]
        for item in await store.claim_batch(
            ItemState.KEYWORD_INDEXED, limit=10, now="2026-02-01T00:01:00Z"
        )
    ]
    assert victim["external_id"] in after_backoff


async def test_mark_retry_dead_letters_after_five_attempts(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(0)])
    item = (await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=1))[0]
    for _ in range(5):
        await store.mark_retry(item["id"], "poison")
    final = await store.get_item(item["id"])
    assert final["state"] == ItemState.FAILED.value
    assert final["attempt_count"] == 5
    assert (await store.counts()).failed == 1
    assert await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10) == []


async def test_state_machine_input_validation(store):
    with pytest.raises(ValueError):
        await store.claim_batch(ItemState.CAPTURED)
    with pytest.raises(ValueError):
        await store.claim_batch("failed")
    with pytest.raises(ValueError):
        await store.claim_batch("bogus")
    with pytest.raises(ValueError):
        await store.mark_stage_done(1, ItemState.CAPTURED)
    with pytest.raises(ValueError):
        await store.mark_stage_done(1, "failed")


# ---------------------------------------------------------------------------
# Embedding pin + reset_vectors
# ---------------------------------------------------------------------------


async def _seed_embedded_item(store: UltraStore, index: int = 0) -> tuple[int, int]:
    """Returns (item_id, document_id) with the item advanced to embedded."""
    await store.upsert_items("src1", [make_item(index)])
    claimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=100)
    target = next(c for c in claimed if c["external_id"] == f"ext-{index:04d}")
    await index_keyword(store, target)
    doc_id = await store.add_document(
        target["id"], DocType.SUMMARY, f"summary text for item {index}"
    )
    return target["id"], doc_id


async def test_embedding_pin_and_dim_mismatch_rejection(store):
    await add_source(store)
    _, doc_id = await _seed_embedded_item(store, 0)
    await store.store_embedding(doc_id, model="model-a", dim=4, vector=[1, 0, 0, 0])
    assert await store.get_meta(META_EMBED_MODEL) == "model-a"
    assert await store.get_meta(META_EMBED_DIM) == "4"

    with pytest.raises(UltraStoreError, match="pinned"):
        await store.store_embedding(doc_id, model="model-a", dim=5, vector=[0] * 5)
    with pytest.raises(UltraStoreError, match="pinned"):
        await store.store_embedding(doc_id, model="model-b", dim=4, vector=[0] * 4)
    with pytest.raises(UltraStoreError, match="components"):
        await store.store_embedding(doc_id, model="model-a", dim=4, vector=[0] * 3)


async def test_reset_vectors_round_trip(store):
    await add_source(store)
    item_id, doc_id = await _seed_embedded_item(store, 0)
    await store.store_embedding(doc_id, model="model-a", dim=4, vector=[1, 0, 0, 0])
    await store.mark_stage_done(item_id, ItemState.EMBEDDED)
    await store.mark_stage_done(item_id, ItemState.DISTILLED)

    await store.reset_vectors()

    # Pins cleared, items back at keyword_indexed, vector leg honestly empty.
    assert await store.get_meta(META_EMBED_MODEL) is None
    assert await store.get_meta(META_EMBED_DIM) is None
    counts = await store.counts()
    assert counts.keyword_indexed == 1
    assert counts.embedded == 0 and counts.distilled == 0
    results, reason = await store.vector_search([1, 0, 0, 0])
    assert results == []
    assert "no embedding has been stored yet" in reason

    # The embedding-model-change path: a new pin with a new dimension works.
    await store.store_embedding(doc_id, model="model-b", dim=8, vector=[0.5] * 8)
    assert await store.get_meta(META_EMBED_MODEL) == "model-b"
    assert await store.get_meta(META_EMBED_DIM) == "8"


# ---------------------------------------------------------------------------
# Vector leg — real extension round-trip + honest degradation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite-vec not installed on this host")
async def test_vector_search_real_roundtrip(store):
    await add_source(store)
    item1, doc1 = await _seed_embedded_item(store, 0)
    item2, doc2 = await _seed_embedded_item(store, 1)
    await store.store_embedding(doc1, model="m", dim=4, vector=[1, 0, 0, 0])
    await store.store_embedding(doc2, model="m", dim=4, vector=[0, 1, 0, 0])

    results, reason = await store.vector_search([1, 0.05, 0, 0], k=2)
    assert reason == ""
    assert [hit.item_id for hit in results] == [item1, item2]
    assert results[0].score > results[1].score
    assert results[0].matched_by == ("vector",)
    assert results[0].permalink == "app://item/0"
    assert all(0.0 <= hit.score <= 1.0 for hit in results)

    # Tombstoned items disappear from the vector leg too.
    await store.upsert_items("src1", [make_item(0, deleted=True)])
    results, reason = await store.vector_search([1, 0.05, 0, 0], k=2)
    assert reason == ""
    assert [hit.item_id for hit in results] == [item2]

    # A query vector that does not fit the pin degrades with a clear reason.
    results, reason = await store.vector_search([1, 0, 0], k=2)
    assert results == []
    assert "pinned" in reason


async def test_vector_search_degrades_honestly_without_sqlite_vec(store, monkeypatch):
    def _broken_import():
        raise ImportError("sqlite-vec is not installed (test)")

    monkeypatch.setattr(store_mod, "_import_sqlite_vec", _broken_import)
    await add_source(store)
    _, doc_id = await _seed_embedded_item(store, 0)

    # Embeddings still accumulate provider-neutrally without the extension.
    await store.store_embedding(doc_id, model="m", dim=4, vector=[1, 0, 0, 0])
    assert await store.get_meta(META_EMBED_DIM) == "4"

    results, reason = await store.vector_search([1, 0, 0, 0])
    assert results == []
    assert "sqlite-vec" in reason
    assert "Keyword search keeps working" in reason


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite-vec not installed on this host")
async def test_vectors_backfill_when_extension_appears_later(tmp_path):
    db_path = tmp_path / "ultrawiki.db"

    with pytest.MonkeyPatch.context() as mp:

        def _broken_import():
            raise ImportError("sqlite-vec is not installed (test)")

        mp.setattr(store_mod, "_import_sqlite_vec", _broken_import)
        crippled = UltraStore(db_path)
        await add_source(crippled)
        _, doc_id = await _seed_embedded_item(crippled, 0)
        await crippled.store_embedding(doc_id, model="m", dim=4, vector=[1, 0, 0, 0])
        results, reason = await crippled.vector_search([1, 0, 0, 0])
        assert results == [] and "sqlite-vec" in reason
        await crippled.close()

    # Same DB file, extension now importable: the vec index is derived from
    # the stored BLOBs — no re-embedding needed.
    healed = UltraStore(db_path)
    results, reason = await healed.vector_search([1, 0, 0, 0])
    assert reason == ""
    assert len(results) == 1
    assert results[0].permalink == "app://item/0"
    await healed.close()


# ---------------------------------------------------------------------------
# Areas, distill cache, sync state, meta
# ---------------------------------------------------------------------------


async def test_areas_crud_and_default_seeding(store):
    default_id = await store.ensure_default_area()
    assert default_id == "default"
    assert await store.ensure_default_area() == "default"  # idempotent

    await store.upsert_area("work", "Work")
    await store.upsert_area("work", "Work & Projects", is_default=True)
    areas = await store.list_areas()
    assert [area["id"] for area in areas if area["is_default"]] == ["work"]
    assert areas[0]["name"] == "Work & Projects"

    await store.delete_area("default")
    assert [area["id"] for area in await store.list_areas()] == ["work"]


async def test_area_scoped_keyword_search(store):
    await add_source(store, "scoped", areas=["work"])
    await store.upsert_items("scoped", [make_item(0)])
    claimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10)
    await index_keyword(store, claimed[0])
    assert len(await store.keyword_search("number0", area_id="work")) == 1
    assert await store.keyword_search("number0", area_id="private") == []


async def test_distill_cache_and_meta_round_trip(store):
    assert await store.distill_cache_get("hash1", 1, "model-x") is None
    await store.distill_cache_put("hash1", 1, "model-x", '{"summary": "s"}')
    assert await store.distill_cache_get("hash1", 1, "model-x") == '{"summary": "s"}'
    # A different prompt version or model is a different cache slot.
    assert await store.distill_cache_get("hash1", 2, "model-x") is None
    assert await store.distill_cache_get("hash1", 1, "model-y") is None

    assert await store.get_meta("missing") is None
    await store.set_meta("k", "v1")
    await store.set_meta("k", "v2")
    assert await store.get_meta("k") == "v2"


async def test_sync_state_partial_updates(store):
    await add_source(store)
    assert await store.get_sync_state("src1") is None
    await store.set_sync_state("src1", cursor="mtime:123")
    state = await store.get_sync_state("src1")
    assert state["cursor"] == "mtime:123"
    assert state["backfill_checkpoint"] is None

    await store.set_sync_state("src1", backfill_checkpoint="ext-0400")
    state = await store.get_sync_state("src1")
    assert state["cursor"] == "mtime:123"  # untouched by the partial update
    assert state["backfill_checkpoint"] == "ext-0400"

    await store.set_sync_state(
        "src1", backfill_complete_at="2026-01-01T00:00:00Z", cursor=None
    )
    state = await store.get_sync_state("src1")
    assert state["cursor"] is None
    assert state["backfill_complete_at"] == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Ranking signals — term rarity + context expansion
# ---------------------------------------------------------------------------


async def seed_indexed(store: UltraStore, items: list[RawItem], source_id: str = "src1") -> None:
    await store.upsert_items(source_id, items)
    for claimed in await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=500):
        await index_keyword(store, claimed)


async def test_live_item_count_ignores_tombstoned_items(store):
    await add_source(store)
    await seed_indexed(store, [make_item(1), make_item(2), make_item(3)])
    assert await store.live_item_count() == 3

    await store.upsert_items("src1", [make_item(2, deleted=True)])
    assert await store.live_item_count() == 2


async def test_term_document_frequency_counts_items_not_occurrences(store):
    await add_source(store)
    await seed_indexed(
        store,
        [
            make_item(1, body="bugatti chiron chiron chiron", title=""),
            make_item(2, body="a note about the chiron", title=""),
            make_item(3, body="something else entirely", title=""),
        ],
    )

    frequencies = await store.term_document_frequency(
        ["chiron", "bugatti", "neverappears"]
    )

    # 'chiron' appears three times inside item 1 but that is ONE document.
    assert frequencies == {"chiron": 2, "bugatti": 1, "neverappears": 0}


async def test_term_document_frequency_tolerates_punctuation_only_tokens(store):
    await add_source(store)
    await seed_indexed(store, [make_item(1)])

    assert await store.term_document_frequency(["!!!", ""]) == {"!!!": 0, "": 0}


async def test_neighbors_for_returns_the_surrounding_thread_messages(store):
    await add_source(store)
    thread = [
        RawItem(
            external_id=f"msg-{index}",
            body=f"message {index} body",
            permalink=f"app://msg/{index}",
            timestamp_utc=f"2026-03-01T10:0{index}:00Z",
            title="",
            thread_key="thread-a",
        )
        for index in range(4)
    ]
    await seed_indexed(store, thread)
    row = await store.get_item_by_external_id("src1", "msg-2")

    neighbors = await store.neighbors_for(int(row["id"]), limit=2)

    # One before, one after — the article's "two neighboring sections".
    assert len(neighbors) == 2
    assert "message 1 body" in neighbors[0]
    assert "message 3 body" in neighbors[1]


async def test_neighbors_for_stays_inside_its_own_thread(store):
    await add_source(store)
    await seed_indexed(
        store,
        [
            RawItem(
                external_id="a1",
                body="mine before",
                permalink="app://a1",
                timestamp_utc="2026-03-01T10:00:00Z",
                thread_key="thread-a",
            ),
            RawItem(
                external_id="a2",
                body="mine anchor",
                permalink="app://a2",
                timestamp_utc="2026-03-01T10:01:00Z",
                thread_key="thread-a",
            ),
            RawItem(
                external_id="b1",
                body="someone elses thread",
                permalink="app://b1",
                timestamp_utc="2026-03-01T10:02:00Z",
                thread_key="thread-b",
            ),
        ],
    )
    row = await store.get_item_by_external_id("src1", "a2")

    neighbors = await store.neighbors_for(int(row["id"]), limit=2)

    assert any("mine before" in text for text in neighbors)
    assert not any("someone elses thread" in text for text in neighbors)


async def test_neighbors_for_falls_back_to_the_items_own_fuller_document(store):
    """File-shaped sources have no thread — the fuller stored rendition is
    the surrounding context instead."""
    await add_source(store)
    await seed_indexed(store, [make_item(7, body="short body", title="Note")])
    row = await store.get_item_by_external_id("src1", "ext-0007")
    item_id = int(row["id"])
    await store.add_document(item_id, DocType.SUMMARY, "the much fuller distilled text")

    neighbors = await store.neighbors_for(item_id, limit=2)

    assert neighbors == ["the much fuller distilled text"]


async def test_neighbors_for_unknown_item_and_zero_limit_are_empty(store):
    await add_source(store)
    await seed_indexed(store, [make_item(1)])
    row = await store.get_item_by_external_id("src1", "ext-0001")

    assert await store.neighbors_for(999_999, limit=2) == []
    assert await store.neighbors_for(int(row["id"]), limit=0) == []


# ---------------------------------------------------------------------------
# Postgres variant — missing driver is an honest, actionable error
# ---------------------------------------------------------------------------


async def test_postgres_missing_driver_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)  # forces ImportError
    with pytest.raises(ImportError, match=r"ultrawiki-postgres"):
        await PostgresStore("postgresql://localhost/x").open()

    ok, message = await PostgresStore.connect_test("postgresql://localhost/x")
    assert ok is False
    assert "ultrawiki-postgres" in message
    assert "SQLite backend keeps working" in message


# ---------------------------------------------------------------------------
# Compare-and-set: a sync racing the pipeline must not corrupt the ladder
# ---------------------------------------------------------------------------


async def test_lost_claim_writes_nothing_and_the_next_pass_recovers(store):
    """A content change between claim and commit invalidates the claim.

    Without the guard the worker stamped its stage onto content that had just
    been reset to 'captured' with its derived rows purged — an item marked
    keyword-indexed that no FTS row covers, or embedded with the OLD vector.
    """
    await add_source(store)
    await store.upsert_items("src1", [make_item(0)])
    claimed = (await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=1))[0]

    # The interleave: a sync lands NEW content for the same external id.
    await store.upsert_items("src1", [make_item(0, body="fresh body omega")])

    committed = await store.mark_stage_done(
        claimed["id"],
        ItemState.KEYWORD_INDEXED,
        fts_title=claimed["title"],
        fts_body=claimed["body_raw"],
        expected_state=claimed["state"],
        expected_content_hash=claimed["content_hash"],
    )
    assert committed is False
    after = await store.get_item(claimed["id"])
    assert after["state"] == ItemState.CAPTURED.value
    # Neither the stale body nor the fresh one is indexed: no FTS write happened.
    assert await store.keyword_search("number0") == []
    assert await store.keyword_search("omega") == []

    # The next pass claims the CURRENT version and commits normally.
    reclaimed = (await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=1))[0]
    assert reclaimed["content_hash"] != claimed["content_hash"]
    assert (
        await store.mark_stage_done(
            reclaimed["id"],
            ItemState.KEYWORD_INDEXED,
            fts_title=reclaimed["title"],
            fts_body=reclaimed["body_raw"],
            expected_state=reclaimed["state"],
            expected_content_hash=reclaimed["content_hash"],
        )
        is True
    )
    assert len(await store.keyword_search("omega")) == 1
    reindexed = await store.get_item(reclaimed["id"])
    assert reindexed["state"] == ItemState.KEYWORD_INDEXED.value


async def test_wrong_expected_state_is_a_lost_claim(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(0)])
    claimed = (await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=1))[0]
    # Another worker already advanced it (state is no longer 'captured').
    await index_keyword(store, claimed)

    committed = await store.mark_stage_done(
        claimed["id"],
        ItemState.EMBEDDED,
        expected_state=ItemState.CAPTURED,
        expected_content_hash=claimed["content_hash"],
    )
    assert committed is False
    assert (await store.get_item(claimed["id"]))["state"] == (
        ItemState.KEYWORD_INDEXED.value
    )


async def test_mark_stage_done_without_a_guard_stays_unconditional(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(0)])
    claimed = (await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=1))[0]
    assert await store.mark_stage_done(claimed["id"], ItemState.KEYWORD_INDEXED) is True


# ---------------------------------------------------------------------------
# requeue_failed — the dead-letter recovery path
# ---------------------------------------------------------------------------


async def test_requeue_failed_restores_each_item_to_its_last_good_stage(store):
    await add_source(store)
    await add_source(store, "src2")
    await store.upsert_items("src1", [make_item(i) for i in range(3)])
    await store.upsert_items("src2", [make_item(9)])
    claimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10)
    by_ext = {item["external_id"]: item for item in claimed}

    # ext-0000 stays raw; ext-0001 is keyword indexed; ext-0002 is embedded too.
    await index_keyword(store, by_ext["ext-0001"])
    await index_keyword(store, by_ext["ext-0002"])
    doc_id = await store.add_document(
        by_ext["ext-0002"]["id"], DocType.RAW, "embedded text", content_hash="h"
    )
    await store.store_embedding(doc_id, model="m", dim=3, vector=[0.1, 0.2, 0.3])
    await store.mark_stage_done(by_ext["ext-0002"]["id"], ItemState.EMBEDDED)

    # Every one of them gives up (the classic case: no chat credential, so the
    # distill stage burned all five attempts on the whole corpus).
    for item in [*claimed]:
        for _ in range(MAX_ATTEMPTS):
            await store.mark_retry(item["id"], "provider had no credit")
    assert (await store.counts()).failed == 4

    moved = await store.requeue_failed("src1")
    assert moved == 3

    states = {
        external_id: (await store.get_item_by_external_id("src1", external_id))["state"]
        for external_id in ("ext-0000", "ext-0001", "ext-0002")
    }
    assert states == {
        "ext-0000": ItemState.CAPTURED.value,
        "ext-0001": ItemState.KEYWORD_INDEXED.value,
        "ext-0002": ItemState.EMBEDDED.value,
    }
    # Retry bookkeeping is cleared, so the pipeline claims them immediately.
    restored = await store.get_item_by_external_id("src1", "ext-0001")
    assert restored["attempt_count"] == 0
    assert restored["next_retry_at"] is None
    assert restored["last_error"] is None
    assert len(await store.claim_batch(ItemState.EMBEDDED, limit=10)) == 1

    # The other source was out of scope and is still dead-lettered.
    assert (await store.counts_for_source("src2")).failed == 1
    assert await store.requeue_failed() == 1  # None = every source
    assert (await store.counts()).failed == 0


async def test_requeue_failed_on_a_healthy_store_is_zero(store):
    await add_source(store)
    await store.upsert_items("src1", [make_item(0)])
    assert await store.requeue_failed() == 0


# ---------------------------------------------------------------------------
# Connection-string errors must never carry the password
# ---------------------------------------------------------------------------


def test_sanitize_conn_error_scrubs_the_stored_dsn():
    dsn = "postgresql://jarvis:sup3r-s3cret@db.internal:5432/uw"
    exc = RuntimeError(f'connection failed: dsn="{dsn}" sslmode=require')
    text = store_mod.sanitize_conn_error(exc, dsn)
    assert "sup3r-s3cret" not in text
    assert dsn not in text
    assert text.startswith("RuntimeError: ")
    assert "***" in text


def test_sanitize_conn_error_scrubs_userinfo_without_a_stored_dsn():
    exc = ValueError("could not translate host in postgres://admin:pw123@h/db")
    text = store_mod.sanitize_conn_error(exc)
    assert "pw123" not in text
    assert "admin" not in text
    assert "postgres://***@h/db" in text


async def test_postgres_connect_test_never_echoes_the_password(monkeypatch):
    dsn = "postgresql://jarvis:hunter2@db.internal:5432/uw"

    class _FakeConnection:
        @staticmethod
        async def connect(conn_str, **_kwargs):
            raise OSError(f'connection to "{conn_str}" refused')

    monkeypatch.setattr(
        store_mod,
        "_import_psycopg",
        lambda: SimpleNamespace(AsyncConnection=_FakeConnection),
    )
    ok, message = await PostgresStore.connect_test(dsn)
    assert ok is False
    assert "hunter2" not in message
    assert "***" in message


async def test_postgres_open_bounds_the_connect_attempt(monkeypatch):
    seen: dict[str, object] = {}

    class _FakeConnection:
        @staticmethod
        async def connect(conn_str, **kwargs):
            seen.update(kwargs)
            raise OSError("refused")

    monkeypatch.setattr(
        store_mod,
        "_import_psycopg",
        lambda: SimpleNamespace(
            AsyncConnection=_FakeConnection, rows=SimpleNamespace(dict_row=object())
        ),
    )
    with pytest.raises(OSError, match="refused"):
        await PostgresStore("postgresql://localhost/x").open()
    # An unreachable host must fail fast into the SQLite fallback, never hang
    # the startup path waiting on the OS default timeout.
    assert seen["connect_timeout"] == store_mod.PG_CONNECT_TIMEOUT_S
