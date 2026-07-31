"""The four P1 roadmap gates (UltraWiki/06-roadmap.md) as automated tests.

(a) a 1000-item backfill interrupted mid-run converges to an identical end
    state on re-run — no duplicates, no gaps;
(b) one poisoned item dead-letters after retries while the other 999 keep
    progressing — nothing is blocked;
(c) a second identical upsert creates ZERO new work — no state resets, no
    new documents (content-hash proof);
(d) the whole file runs offline with no credentials and no optional
    dependencies (sqlite-vec / psycopg both unavailable).
"""

from __future__ import annotations

import pytest

import jarvis.ultrawiki.store as store_mod
from jarvis.ultrawiki.store import PostgresStore, UltraStore
from jarvis.ultrawiki.types import ConsentState, DocType, ItemState, RawItem


def make_items(count: int) -> list[RawItem]:
    items = []
    for index in range(count):
        hours, rem = divmod(index, 3600)
        minutes, seconds = divmod(rem, 60)
        items.append(
            RawItem(
                external_id=f"ext-{index:04d}",
                body=f"body text number{index} alpha",
                permalink=f"app://item/{index}",
                timestamp_utc=(
                    f"2026-01-01T{hours % 24:02d}:{minutes:02d}:{seconds:02d}Z"
                ),
                title=f"Item {index}",
            )
        )
    return items


@pytest.fixture
async def store(tmp_path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    await instance.upsert_source(
        "src1", connector="local-folder", label="Gate source"
    )
    await instance.set_consent("src1", ConsentState.APPROVED)
    yield instance
    await instance.close()


async def index_keyword(store: UltraStore, claimed: dict) -> None:
    await store.mark_stage_done(
        claimed["id"],
        ItemState.KEYWORD_INDEXED,
        fts_title=claimed["title"],
        fts_body=claimed["body_raw"],
    )


async def test_gate_a_interrupted_backfill_converges(store):
    """Upsert in two chunks (simulating a mid-run kill), then re-run the
    FULL set: identical end state, no duplicates, no gaps, no resets."""
    items = make_items(1000)

    # First run dies after 600 items ...
    first = await store.upsert_items("src1", items[:600])
    assert first.new == 600

    # ... having already advanced 300 of them through the keyword stage.
    for claimed in await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=300):
        await index_keyword(store, claimed)

    # The restarted backfill re-yields EVERYTHING (unparsable-checkpoint
    # convention: safe full re-yield, idempotent upserts).
    second = await store.upsert_items("src1", items)
    assert (second.new, second.changed, second.unchanged) == (400, 0, 600)

    counts = await store.counts()
    assert counts.total == 1000  # no dupes (UNIQUE upsert), no gaps
    assert counts.keyword_indexed == 300  # finished work survived untouched
    assert counts.captured == 700

    # Drain the remaining work; the ladder converges completely.
    remaining = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=2000)
    assert len(remaining) == 700
    for claimed in remaining:
        await index_keyword(store, claimed)
    counts = await store.counts()
    assert counts.keyword_indexed == 1000
    assert counts.captured == 0

    # A third full re-run is a pure no-op.
    third = await store.upsert_items("src1", items)
    assert (third.new, third.changed, third.unchanged) == (0, 0, 1000)
    assert (await store.counts()).keyword_indexed == 1000


async def test_gate_b_poisoned_item_blocks_nothing(store):
    """One item dead-letters after 5 retries; the other 999 progress."""
    await store.upsert_items("src1", make_items(1000))

    claimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=1000)
    assert len(claimed) == 1000
    poison, healthy = claimed[0], claimed[1:]

    for _ in range(5):
        await store.mark_retry(poison["id"], "connector exploded at this item")
    for item in healthy:
        await index_keyword(store, item)

    counts = await store.counts()
    assert counts.failed == 1
    assert counts.keyword_indexed == 999
    assert counts.captured == 0

    # The dead-lettered item never reappears in any claim; the healthy 999
    # are claimable for the next stage.
    assert await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10) == []
    next_stage = await store.claim_batch(ItemState.EMBEDDED, limit=2000)
    assert len(next_stage) == 999
    assert poison["id"] not in {item["id"] for item in next_stage}
    failed_item = await store.get_item(poison["id"])
    assert failed_item["state"] == ItemState.FAILED.value
    assert failed_item["last_error"] == "connector exploded at this item"


async def test_gate_c_second_identical_upsert_creates_zero_new_work(store):
    """Content-hash proof: an identical re-run touches nothing at all."""
    items = make_items(200)
    await store.upsert_items("src1", items)
    for claimed in await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=200):
        await index_keyword(store, claimed)
    sample = await store.claim_batch(ItemState.EMBEDDED, limit=1)
    doc_id = await store.add_document(
        sample[0]["id"], DocType.SUMMARY, "distilled summary"
    )

    before = {}
    for index in range(200):
        item = await store.get_item_by_external_id("src1", f"ext-{index:04d}")
        before[item["external_id"]] = item

    rerun = await store.upsert_items("src1", items)
    assert (rerun.new, rerun.changed, rerun.unchanged) == (0, 0, 200)

    for index in range(200):
        item = await store.get_item_by_external_id("src1", f"ext-{index:04d}")
        assert item == before[item["external_id"]]  # byte-identical rows

    # Derived work survived (no document/FTS churn) and search still answers.
    # With foreign_keys=ON this embedding insert would fail if the rerun had
    # purged the document — its success proves the document row survived.
    assert len(await store.keyword_search("number7")) == 1
    await store.store_embedding(doc_id, model="m", dim=4, vector=[1, 0, 0, 0])


async def test_gate_d_runs_offline_with_no_optional_deps_and_no_credentials(
    tmp_path, monkeypatch
):
    """The store pipeline is complete with ZERO optional dependencies: no
    sqlite-vec, no psycopg, no network, no credentials — the universal
    headless-Linux floor (CLAUDE.md section 3)."""

    def _no_sqlite_vec():
        raise ImportError("sqlite-vec unavailable (gate d)")

    def _no_psycopg():
        raise ImportError(
            "PostgresStore requires the 'psycopg' driver, which is not "
            "installed. Install it with: pip install "
            "personal-jarvis[ultrawiki-postgres]. The SQLite backend keeps "
            "working without it."
        )

    monkeypatch.setattr(store_mod, "_import_sqlite_vec", _no_sqlite_vec)
    monkeypatch.setattr(store_mod, "_import_psycopg", _no_psycopg)

    store = UltraStore(tmp_path / "floor.db")
    try:
        await store.upsert_source("src1", connector="local-folder", label="Floor")
        await store.set_consent("src1", ConsentState.APPROVED)
        await store.upsert_items("src1", make_items(10))
        for claimed in await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=10):
            await index_keyword(store, claimed)

        # Keyword search: fully functional.
        assert len(await store.keyword_search("number3")) == 1

        # Embeddings accumulate; the vector leg degrades honestly.
        item = (await store.claim_batch(ItemState.EMBEDDED, limit=1))[0]
        doc_id = await store.add_document(item["id"], DocType.SUMMARY, "summary")
        await store.store_embedding(doc_id, model="m", dim=4, vector=[1, 0, 0, 0])
        results, reason = await store.vector_search([1, 0, 0, 0])
        assert results == []
        assert reason  # an honest English explanation, never a crash

        # The distillation cache works without any provider.
        await store.distill_cache_put("h", 1, "m", "{}")
        assert await store.distill_cache_get("h", 1, "m") == "{}"

        # The Postgres option reports its missing driver honestly.
        ok, message = await PostgresStore.connect_test("postgresql://localhost/x")
        assert ok is False
        assert "ultrawiki-postgres" in message
    finally:
        await store.close()
