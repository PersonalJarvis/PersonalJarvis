"""PipelineWorker unit tests — real UltraStore on tmp_path, fully offline.

Covers: the full captured -> keyword_indexed -> embedded -> distilled ladder
with a fake embedding backend and fake distill_fn; the honest backlog when the
embedding slot is unconfigured; retry-into-dead-letter for a poisoned distill
item while healthy items pass; the distill cache short-circuit; and clean
cancel-event / hard-cancel shutdown. No network, no credentials, no models.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.ultrawiki.pipeline import EMBED_BATCH, MAX_EMBED_CHARS, PipelineWorker
from jarvis.ultrawiki.store import UltraStore
from jarvis.ultrawiki.types import ConsentState, ItemState, RawItem

VECTOR = [0.1, 0.2, 0.3]

#: The distillation gate, stated explicitly in every test: these workers use an
#: INJECTED distiller, so the production credential-chain probe must never run
#: — a test whose outcome depends on the host's keys is the AP-23 trap.
DISTILL_READY = lambda: (True, "")  # noqa: E731 — a one-line test seam


def make_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            enabled=True,
            db_backend="sqlite",
            embedding_provider="fake",
            embedding_model="fake-model",
            distill_provider="",
            distill_model="",
            rerank_provider="",
            ollama_endpoint="",
        ),
        memory=SimpleNamespace(data_dir="unused"),
        brain=SimpleNamespace(primary=""),
    )


class FakeEmbeddingBackend:
    """Fixed-vector backend implementing the EmbeddingBackend protocol.

    ``poison`` marks a substring that makes ``embed`` raise whenever a batch
    contains it — the "one bad text in a batch of 32" case.
    """

    name = "fake"

    def __init__(self, *, usable: bool = True, poison: str = "") -> None:
        self.usable = usable
        self.poison = poison
        self.embed_calls: list[list[str]] = []

    def ready(self) -> tuple[bool, str]:
        return (True, "") if self.usable else (False, "fake backend is down")

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        if self.poison and any(self.poison in text for text in texts):
            raise RuntimeError("provider rejected the request (400)")
        return [list(VECTOR) for _ in texts]


class FakeDistillResult:
    """Shape-compatible stand-in for jarvis.ultrawiki.distill.DistillResult."""

    def __init__(self, *, question: str, summary: str) -> None:
        self.question = question
        self.summary = summary
        self.resolution = "Resolved."
        self.entities = ["Alpha"]
        self.refs: list[str] = []
        self.raw_json = json.dumps(
            {
                "question": question,
                "summary": summary,
                "resolution": self.resolution,
                "entities": self.entities,
                "refs": self.refs,
            }
        )


def make_item(index: int, *, title: str | None = None, body: str | None = None) -> RawItem:
    return RawItem(
        external_id=f"ext-{index:04d}",
        body=body if body is not None else f"body text number {index}",
        permalink=f"app://item/{index}",
        timestamp_utc=f"2026-01-01T00:{index % 60:02d}:00Z",
        title=title if title is not None else f"Item {index}",
    )


@pytest.fixture
async def store(tmp_path: Path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    await instance.upsert_source(
        "src1", connector="local-folder", label="Test source"
    )
    await instance.set_consent("src1", ConsentState.APPROVED)
    yield instance
    await instance.close()


def db_scalar(db_path: Path, sql: str, params: tuple = ()) -> Any:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


async def distill_ok(cfg: Any, *, title: str, body: str, source_kind: str) -> Any:
    return FakeDistillResult(question=f"What about {title}?", summary=f"Summary of {title}.")


async def distill_never(cfg: Any, *, title: str, body: str, source_kind: str) -> Any:
    raise AssertionError("distill_fn must not be called in this test")


# ---------------------------------------------------------------------------
# Full ladder
# ---------------------------------------------------------------------------


async def test_full_ladder_captured_to_distilled(store, tmp_path):
    await store.upsert_items("src1", [make_item(1)])
    backend = FakeEmbeddingBackend()
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_ok,
        distill_ready_fn=DISTILL_READY,
    )

    attempted = await worker.run_once()
    assert attempted >= 3  # one item worked in each of the three stages

    item = await store.get_item_by_external_id("src1", "ext-0001")
    assert item is not None
    assert item["state"] == ItemState.DISTILLED.value
    assert worker.processed_counts() == {"keyword": 1, "embed": 1, "distill": 1}

    # RAW + SUMMARY documents exist, each with a stored embedding.
    db_path = tmp_path / "ultrawiki.db"
    assert db_scalar(db_path, "SELECT COUNT(*) FROM uw_documents") == 2
    assert db_scalar(db_path, "SELECT COUNT(*) FROM uw_embeddings") == 2
    assert (
        db_scalar(
            db_path,
            "SELECT COUNT(*) FROM uw_documents WHERE doc_type = 'summary'"
            " AND distill_json IS NOT NULL",
        )
        == 1
    )
    # Keyword leg is live from the first stage.
    hits = await store.keyword_search("body")
    assert hits and hits[0].item_id == item["id"]
    # The distillation result was cached.
    assert db_scalar(db_path, "SELECT COUNT(*) FROM uw_distill_cache") == 1
    # The summary embedding used the composed summary text, not the raw body.
    assert any("What about Item 1?" in texts[0] for texts in backend.embed_calls if texts)


# ---------------------------------------------------------------------------
# Unconfigured embedding slot — honest backlog
# ---------------------------------------------------------------------------


async def test_unconfigured_slot_stops_at_keyword_indexed(store):
    await store.upsert_items("src1", [make_item(1), make_item(2)])
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: None,  # slot unconfigured
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    await worker.run_once()
    await worker.run_once()

    counts = await store.counts()
    assert counts.keyword_indexed == 2
    assert counts.embedded == 0
    assert counts.distilled == 0
    assert worker.processed_counts() == {"keyword": 2, "embed": 0, "distill": 0}


async def test_not_ready_backend_claims_no_embed_work(store):
    await store.upsert_items("src1", [make_item(1)])
    backend = FakeEmbeddingBackend(usable=False)
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    await worker.run_once()

    counts = await store.counts()
    assert counts.keyword_indexed == 1
    assert counts.embedded == 0
    assert backend.embed_calls == []


# ---------------------------------------------------------------------------
# Poisoned distill item — retries into failed while others pass
# ---------------------------------------------------------------------------


async def test_poison_distill_dead_letters_while_others_pass(store):
    await store.upsert_items(
        "src1",
        [make_item(1, title="Good"), make_item(2, title="Poison")],
    )

    async def flaky_distill(cfg: Any, *, title: str, body: str, source_kind: str) -> Any:
        if title == "Poison":
            raise RuntimeError("provider exploded")
        return FakeDistillResult(question="Q?", summary="S.")

    clock = {"now": datetime(2026, 3, 1, 12, 0, tzinfo=UTC)}
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=flaky_distill,
        now_fn=lambda: clock["now"],
        distill_ready_fn=DISTILL_READY,
    )

    # Backoff ladder tops out at 3840 s before the 5th attempt dead-letters;
    # a 2 h clock step keeps every retry eligible on the next pass.
    for _ in range(6):
        await worker.run_once()
        clock["now"] += timedelta(hours=2)

    good = await store.get_item_by_external_id("src1", "ext-0001")
    poison = await store.get_item_by_external_id("src1", "ext-0002")
    assert good is not None and good["state"] == ItemState.DISTILLED.value
    assert poison is not None
    assert poison["state"] == ItemState.FAILED.value
    assert "distill" in (poison["last_error"] or "")
    assert worker.processed_counts()["distill"] == 1

    counts = await store.counts()
    assert counts.distilled == 1
    assert counts.failed == 1


# ---------------------------------------------------------------------------
# Distill cache — identical content is never paid for twice
# ---------------------------------------------------------------------------


async def test_distill_cache_hit_skips_the_provider_call(store):
    twin_kwargs = {"title": "Twin", "body": "identical body content"}
    await store.upsert_items(
        "src1", [make_item(1, **twin_kwargs), make_item(2, **twin_kwargs)]
    )
    calls = {"n": 0}

    async def counting_distill(cfg: Any, *, title: str, body: str, source_kind: str) -> Any:
        calls["n"] += 1
        return FakeDistillResult(question="Q?", summary="S.")

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=counting_distill,
        distill_ready_fn=DISTILL_READY,
    )

    await worker.run_once()

    assert calls["n"] == 1  # second twin rode the cache
    counts = await store.counts()
    assert counts.distilled == 2
    assert worker.processed_counts()["distill"] == 2


# ---------------------------------------------------------------------------
# Shutdown discipline
# ---------------------------------------------------------------------------


async def test_cancel_event_stops_the_loop_cleanly(store):
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: None,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    cancel = asyncio.Event()
    task = asyncio.create_task(worker.run(cancel), name="test-uw-pipeline")

    await asyncio.sleep(0.05)
    assert not task.done()
    cancel.set()
    await asyncio.wait_for(task, timeout=1.0)  # wakes from the idle sleep
    assert task.done() and not task.cancelled()


# ---------------------------------------------------------------------------
# One bad text must not dead-letter its whole batch
# ---------------------------------------------------------------------------


async def test_batch_embed_failure_retries_members_individually(store):
    """31 healthy items advance; only the poisoned one accrues an attempt."""
    count = EMBED_BATCH
    items = [make_item(i) for i in range(count)]
    items[7] = make_item(7, body="this body makes the provider choke")
    await store.upsert_items("src1", items)
    backend = FakeEmbeddingBackend(poison="choke")
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    await worker._keyword_pass()  # noqa: SLF001 — drive one stage deliberately
    await worker._embed_pass()  # noqa: SLF001

    assert worker.processed_counts()["embed"] == count - 1
    counts = await store.counts()
    assert counts.embedded == count - 1
    assert counts.keyword_indexed == 1  # the poisoned one kept its good state

    poisoned = await store.get_item_by_external_id("src1", "ext-0007")
    assert poisoned["attempt_count"] == 1
    assert "embed" in (poisoned["last_error"] or "")
    # Every healthy neighbour is untouched by the poisoned member's failure.
    healthy = await store.get_item_by_external_id("src1", "ext-0006")
    assert healthy["state"] == ItemState.EMBEDDED.value
    assert healthy["attempt_count"] == 0
    assert healthy["last_error"] is None
    # One batch call, then one call per member (the individual retry pass).
    assert len(backend.embed_calls) == 1 + count


async def test_embed_input_is_capped_to_the_provider_budget(store):
    await store.upsert_items(
        "src1", [make_item(1, body="x" * (MAX_EMBED_CHARS * 3))]
    )
    backend = FakeEmbeddingBackend()
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    await worker._keyword_pass()  # noqa: SLF001
    await worker._embed_pass()  # noqa: SLF001

    assert backend.embed_calls
    assert len(backend.embed_calls[0][0]) == MAX_EMBED_CHARS
    assert (await store.counts()).embedded == 1


# ---------------------------------------------------------------------------
# The distillation gate — a keyless install pauses instead of dead-lettering
# ---------------------------------------------------------------------------


async def test_unready_distill_slot_claims_no_work_and_never_fails_items(store):
    await store.upsert_items("src1", [make_item(1), make_item(2)])
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=distill_never,  # asserts it is never called
        distill_ready_fn=lambda: (False, "no credential-ready chat provider"),
    )

    for _ in range(6):  # more passes than MAX_ATTEMPTS would need to give up
        await worker.run_once()

    counts = await store.counts()
    assert counts.embedded == 2
    assert counts.distilled == 0
    assert counts.failed == 0  # nothing was dead-lettered
    everything = await store.get_item_by_external_id("src1", "ext-0001")
    assert everything["attempt_count"] == 0
    assert everything["last_error"] is None


async def test_distill_resumes_once_the_slot_becomes_ready(store):
    await store.upsert_items("src1", [make_item(1)])
    slot = {"ready": False}
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=distill_ok,
        distill_ready_fn=lambda: (
            (True, "") if slot["ready"] else (False, "no chat provider yet")
        ),
    )

    await worker.run_once()
    assert (await store.counts()).embedded == 1

    slot["ready"] = True
    worker._distill_ready_cache = None  # noqa: SLF001 — skip the 30 s probe TTL
    await worker.run_once()
    assert (await store.counts()).distilled == 1


# ---------------------------------------------------------------------------
# Lost claims (a sync racing the pipeline) are skipped, never retried
# ---------------------------------------------------------------------------


async def test_lost_claim_is_skipped_without_charging_an_attempt(store):
    """A content change mid-flight must not look like a stage failure."""
    await store.upsert_items("src1", [make_item(1)])

    class RacingStore:
        """Delegates to the real store but rewrites the item's content the
        moment the keyword stage tries to commit."""

        def __init__(self, inner: UltraStore) -> None:
            self._inner = inner
            self.raced = False

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        async def mark_stage_done(self, item_id: int, new_state: Any, **kwargs: Any):
            if not self.raced and new_state == ItemState.KEYWORD_INDEXED:
                self.raced = True
                await self._inner.upsert_items(
                    "src1", [make_item(1, body="rewritten by a racing sync")]
                )
            return await self._inner.mark_stage_done(item_id, new_state, **kwargs)

    racing = RacingStore(store)
    worker = PipelineWorker(
        racing,
        make_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=distill_ok,
        distill_ready_fn=DISTILL_READY,
    )

    await worker._keyword_pass()  # noqa: SLF001 — the raced stage

    assert racing.raced is True
    assert worker.processed_counts()["keyword"] == 0  # the claim was dropped
    item = await store.get_item_by_external_id("src1", "ext-0001")
    assert item["state"] == ItemState.CAPTURED.value
    assert item["attempt_count"] == 0  # a lost claim is not a failure
    assert item["last_error"] is None

    # The next pass works on the NEW content and completes the whole ladder.
    await worker.run_once()
    item = await store.get_item_by_external_id("src1", "ext-0001")
    assert item["state"] == ItemState.DISTILLED.value
    assert len(await store.keyword_search("rewritten")) == 1


async def test_hard_cancel_reraises_cancelled_error(store):
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: None,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    task = asyncio.create_task(worker.run(asyncio.Event()), name="test-uw-pipeline-2")
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
