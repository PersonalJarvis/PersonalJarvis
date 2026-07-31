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
from jarvis.ultrawiki.store import MAX_ATTEMPTS, EmbeddingSpaceMismatch, UltraStore
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


async def test_pipeline_drains_legacy_tombstones_during_normal_passes(
    store, monkeypatch
):
    calls: list[int] = []

    async def repair(*, limit: int) -> int:
        calls.append(limit)
        return 2

    monkeypatch.setattr(store, "repair_legacy_tombstones", repair)
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: None,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    assert await worker.run_once() == 2
    assert calls == [5000]


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
# A vector-space pin the config has outgrown (forensic 2026-07-28)
# ---------------------------------------------------------------------------


async def test_a_stale_vector_space_pin_is_reconciled_not_dead_lettered(store):
    """Changing the embedding model outside PUT /settings must still work.

    The maintainer switched the model with the Normal/Ultra switch — a real,
    visible UI act. That route wrote the config and never registered the switch
    with the store, so every vector the new provider produced was rejected. The
    lane failed 100 % of its work for a day, and because the rejection was
    charged as a per-item retry, the corpus was five attempts away from
    dead-lettering itself item by item while the screen said "still filling up".

    The worker now reconciles with the model it actually resolved, so the
    rebuild starts on the next pass no matter who changed the setting.
    """
    await store.upsert_items("src1", [make_item(1)])
    cfg = make_cfg()
    backend = FakeEmbeddingBackend()
    worker = PipelineWorker(
        store,
        cfg,
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    await worker.run_once()
    assert (await store.counts()).embedded == 1  # pin is now "fake-model"

    # The switch, exactly as a bypassing route leaves it: config says one
    # model, the store is still pinned to the other.
    cfg.ultrawiki.embedding_model = "other-model"
    await store.upsert_items("src1", [make_item(2)])

    for _ in range(MAX_ATTEMPTS + 1):
        await worker.run_once()

    # Not one item was charged an attempt, let alone dead-lettered.
    counts = await store.counts()
    assert counts.failed == 0
    item = await store.get_item_by_external_id("src1", "ext-0002")
    assert item is not None
    assert item["attempt_count"] == 0

    # And the lane is moving again: the switch was registered as a background
    # rebuild, with the live vectors still serving search until it completes.
    assert (await store.reembed_status())["model"] == "other-model"
    assert item["state"] == ItemState.EMBEDDED.value


async def test_a_space_mismatch_pauses_the_lane_and_charges_no_attempt(store):
    """The second half of the same defect, isolated.

    Reconciliation cannot close every gap — a provider may answer with a model
    id nobody configured. Whatever the cause, a vector-space mismatch is a
    CONFIGURATION fault: it says nothing about the item that happened to be in
    the batch, so charging it an attempt (and dead-lettering it on the fifth)
    destroys innocent content to report a settings problem.
    """
    await store.upsert_items("src1", [make_item(1)])

    async def refuse(*args: Any, **kwargs: Any) -> None:
        raise EmbeddingSpaceMismatch("embedding space mismatch: pinned to model-a")

    async def already_fine(model: str) -> str:
        return "active"  # reconciliation sees nothing to do

    store.store_embedding = refuse  # type: ignore[method-assign]
    store.reconcile_space = already_fine  # type: ignore[method-assign]
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    for _ in range(MAX_ATTEMPTS + 1):
        await worker.run_once()

    item = await store.get_item_by_external_id("src1", "ext-0001")
    assert item is not None
    # The backlog waits, intact, exactly where it was.
    assert item["state"] == ItemState.KEYWORD_INDEXED.value
    assert item["attempt_count"] == 0
    assert (await store.counts()).failed == 0


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


async def test_quota_rejection_rests_the_stage_instead_of_hammering(store):
    """A 429/quota answer is GLOBAL: no per-item fan-out (one 429 used to
    become 33 calls per pass), no attempts charged, and the next pass claims
    nothing while the cooldown runs."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    await store.upsert_items("src1", [make_item(i) for i in range(5)])

    class QuotaBackend(FakeEmbeddingBackend):
        async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
            self.embed_calls.append(list(texts))
            raise EmbeddingError("fake: embedding request failed with HTTP 429")

    backend = QuotaBackend()
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    await worker._keyword_pass()  # noqa: SLF001 — drive one stage deliberately
    await worker._embed_pass()  # noqa: SLF001 — the 429 batch call
    await worker._embed_pass()  # noqa: SLF001 — cooldown: must not call again

    assert len(backend.embed_calls) == 1, "no per-item fan-out, no re-claim"
    item = await store.get_item_by_external_id("src1", "ext-0000")
    assert item["attempt_count"] == 0, "a global outage charges nobody"
    assert item["state"] == ItemState.KEYWORD_INDEXED.value
    assert item["last_error"] is None


async def test_embed_work_resumes_after_the_cooldown(store):
    from jarvis.ultrawiki.embeddings import EmbeddingError

    await store.upsert_items("src1", [make_item(1)])

    class HealingBackend(FakeEmbeddingBackend):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = True

        async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
            if self.fail_next:
                self.fail_next = False
                raise EmbeddingError("fake: embedding request failed with HTTP 429")
            return await super().embed(texts, model=model)

    backend = HealingBackend()
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    await worker._keyword_pass()  # noqa: SLF001
    await worker._embed_pass()  # noqa: SLF001 — 429, cooldown starts
    worker._embed_cooldown_until = 0.0  # noqa: SLF001 — cooldown elapsed
    await worker._embed_pass()  # noqa: SLF001

    assert worker.processed_counts()["embed"] == 1
    item = await store.get_item_by_external_id("src1", "ext-0001")
    assert item["state"] == ItemState.EMBEDDED.value


@pytest.mark.parametrize(
    "message",
    [
        "fake: embedding request failed (ConnectError)",
        "fake: embedding request failed (ConnectTimeout)",
        "fake: embedding request failed with HTTP 401",
        "fake: embedding request failed with HTTP 502",
    ],
)
async def test_provider_wide_embed_failures_do_not_poison_items(store, message):
    """Network, auth and upstream failures pause the slot without charging
    attempts to whichever item happened to be in flight."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    await store.upsert_items("src1", [make_item(1)])

    class BrokenBackend(FakeEmbeddingBackend):
        async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
            self.embed_calls.append(list(texts))
            raise EmbeddingError(message)

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: BrokenBackend(),
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )

    await worker._keyword_pass()  # noqa: SLF001
    await worker._embed_pass()  # noqa: SLF001

    item = await store.get_item_by_external_id("src1", "ext-0001")
    assert item["attempt_count"] == 0
    assert item["state"] == ItemState.KEYWORD_INDEXED.value
    assert worker.embed_block() is not None


async def test_distill_pass_rests_through_the_embed_cooldown(store):
    """The distilled summary must be embedded too, so the distill stage
    shares the provider cooldown instead of burning chat-model calls whose
    result cannot be stored."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    backend = FakeEmbeddingBackend()
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,  # asserts if the stage claims work
        distill_ready_fn=DISTILL_READY,
    )
    worker._begin_embed_cooldown(  # noqa: SLF001
        EmbeddingError("fake: embedding request failed with HTTP 429")
    )

    assert await worker._distill_pass() == 0  # noqa: SLF001


async def test_an_exhausted_quota_is_reported_as_needing_a_human(store):
    """The forensic case of 2026-07-27.

    An OpenAI key with no credit answers ``HTTP 429 (insufficient_quota)``
    forever. The cooldown handled it correctly and silently, so the worker
    napped ten minutes at a time for fifteen hours while every surface above
    reported a healthy import. The block is now visible, and it says whether
    waiting is the fix.
    """
    from jarvis.ultrawiki.embeddings import EmbeddingError

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: FakeEmbeddingBackend(),
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    worker._begin_embed_cooldown(  # noqa: SLF001 — drive the branch deliberately
        EmbeddingError("fake: embedding request failed with HTTP 429 (insufficient_quota)")
    )

    block = worker.embed_block()
    assert block is not None
    assert block["needs_attention"] is True, "no amount of waiting adds credit"
    assert "insufficient_quota" in block["reason"]
    assert block["rejections"] == 1
    assert block["since"].endswith("Z")


async def test_an_ordinary_rate_limit_does_not_cry_for_help(store):
    """The other half of the same 429: going too fast fixes itself, and a
    surface that shouts about it teaches people to ignore the shouting."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: FakeEmbeddingBackend(),
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    worker._begin_embed_cooldown(  # noqa: SLF001
        EmbeddingError("fake: embedding request failed with HTTP 429 (rate_limit_exceeded)")
    )

    block = worker.embed_block()
    assert block is not None
    assert block["needs_attention"] is False


async def test_the_block_dates_from_the_first_refusal_not_the_latest_nap(store):
    """What a reader needs is "refused since yesterday evening", not "this
    ten-minute rest started a moment ago"."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: FakeEmbeddingBackend(),
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    error = EmbeddingError("fake: embedding request failed with HTTP 429 (insufficient_quota)")
    worker._begin_embed_cooldown(error)  # noqa: SLF001
    first_seen = worker.embed_block()["since"]
    worker._begin_embed_cooldown(error)  # noqa: SLF001
    worker._begin_embed_cooldown(error)  # noqa: SLF001

    block = worker.embed_block()
    assert block["since"] == first_seen, "the streak keeps its start"
    assert block["rejections"] == 3


async def test_a_vector_coming_back_clears_the_block_without_a_restart(store):
    """Recovery is the provider's to announce, and it announces it by
    answering. A topped-up account must resume on its own."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    await store.upsert_items("src1", [make_item(1)])
    backend = FakeEmbeddingBackend()
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    worker._begin_embed_cooldown(  # noqa: SLF001
        EmbeddingError("fake: embedding request failed with HTTP 429 (insufficient_quota)")
    )
    assert worker.embed_block() is not None

    worker._embed_cooldown_until = 0.0  # noqa: SLF001 — cooldown elapsed
    await worker._keyword_pass()  # noqa: SLF001
    await worker._embed_pass()  # noqa: SLF001 — the provider answers again

    assert worker.embed_block() is None
    assert worker.processed_counts()["embed"] == 1


async def test_switching_the_backend_resumes_at_once_instead_of_serving_the_nap(store):
    """Taking the advice has to work immediately.

    A blocked stage tells the user to add credit *or switch the embedding
    backend*. Following that used to change nothing for up to ten minutes: the
    cooldown was global and the block did not record whose it was, so a
    freshly chosen, healthy backend sat out the dead one's rest while the UI
    kept quoting the dead one's complaint (observed live 2026-07-27).
    """
    from jarvis.ultrawiki.embeddings import EmbeddingError

    await store.upsert_items("src1", [make_item(1)])
    healthy = FakeEmbeddingBackend()
    healthy.name = "other-provider"
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: healthy,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    # A standstill earned by the PREVIOUS slot, cooldown still running.
    worker._embed_slot_key = "dead-provider:some-model"  # noqa: SLF001
    worker._begin_embed_cooldown(  # noqa: SLF001
        EmbeddingError("dead-provider: embedding request failed with HTTP 429 (insufficient_quota)")
    )
    assert worker.embed_block() is not None

    await worker._keyword_pass()  # noqa: SLF001
    await worker._embed_pass()  # noqa: SLF001 — the NEW slot owes nothing

    assert worker.embed_block() is None, "a new backend starts clean"
    assert worker.processed_counts()["embed"] == 1
    item = await store.get_item_by_external_id("src1", "ext-0001")
    assert item["state"] == ItemState.EMBEDDED.value


async def test_a_block_survives_a_pass_on_the_same_slot(store):
    """The other half: forgetting on every pass would forget everything."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    backend = FakeEmbeddingBackend()
    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: backend,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    await worker._embedding_slot()  # noqa: SLF001 — record the live slot
    worker._begin_embed_cooldown(  # noqa: SLF001
        EmbeddingError("fake: embedding request failed with HTTP 429 (insufficient_quota)")
    )

    await worker._embed_pass()  # noqa: SLF001 — same slot, still resting

    block = worker.embed_block()
    assert block is not None
    assert block["slot"] == "fake:fake-model"
    assert backend.embed_calls == [], "the cooldown still holds"


async def test_a_standstill_is_logged_once_not_every_countdown_tick(store, caplog):
    """The pause sentence carries a live countdown, so deduplicating on the
    text logged the same standstill every time the minute changed — 2 614
    identical lines in one session, which is how a real block hides."""
    from jarvis.ultrawiki.embeddings import EmbeddingError

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: FakeEmbeddingBackend(),
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    # Resolve the slot first: a rejection can only ever be earned by a slot
    # the stage already resolved, and the block is attributed to it.
    await worker._embedding_slot()  # noqa: SLF001
    worker._begin_embed_cooldown(  # noqa: SLF001
        EmbeddingError("fake: embedding request failed with HTTP 429 (insufficient_quota)")
    )
    with caplog.at_level("INFO", logger="jarvis.ultrawiki.pipeline"):
        for _ in range(5):
            await worker._embed_pass()  # noqa: SLF001

    paused = [r for r in caplog.records if "embed stage paused" in r.getMessage()]
    assert len(paused) == 1, "one standstill, one line"


async def test_a_long_item_is_embedded_as_many_passages_not_truncated(store):
    """The change that makes full-depth ingestion mean anything.

    A long item used to be cut at MAX_EMBED_CHARS and given ONE vector, so
    everything past the opening paragraph was stored but unsearchable by
    meaning. It now becomes many passages, each its own document and vector,
    together covering the whole body — which is the only reason pulling more
    data changes what a user can retrieve.
    """
    body = "\n\n".join(
        f"Paragraph {i} discussing subject {i}. " * 6 for i in range(200)
    )
    await store.upsert_items("src1", [make_item(1, body=body)])
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

    # embed_calls[0] IS the list of texts sent in that batch call.
    sent = backend.embed_calls[0]
    assert len(sent) > 5, "a long body must produce many passages"
    # Each passage stays inside the provider budget…
    assert all(len(text) <= MAX_EMBED_CHARS for text in sent)
    # …and text far past the old 8 000-char cut is now genuinely embedded.
    assert any("Paragraph 199" in text for text in sent)
    # One document AND one vector per passage, not one per item.
    docs = await store.item_documents(1)
    assert len(docs) == len(sent)
    assert all(doc["has_vector"] for doc in docs)
    assert [doc["chunk_index"] for doc in docs] == list(range(len(docs)))
    assert (await store.counts()).embedded == 1


async def test_every_passage_carries_the_item_title(store):
    """A vector for "line 4 200 of a file" is unretrievable without its name."""
    body = "\n\n".join(f"Section {i}. " + "detail " * 80 for i in range(40))
    await store.upsert_items("src1", [make_item(1, title="Ledger notes", body=body)])
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

    # embed_calls[0] IS the list of texts sent in that batch call.
    sent = backend.embed_calls[0]
    assert len(sent) > 1
    assert all(text.startswith("Ledger notes") for text in sent)


async def test_re_embedding_replaces_the_passages_instead_of_adding_more(store):
    """Otherwise a second pass doubles every item's vectors."""
    body = "\n\n".join(f"Line {i} " + "word " * 60 for i in range(30))
    await store.upsert_items("src1", [make_item(1, body=body)])
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
    first = len(await store.item_documents(1))

    await store.reset_vectors()
    await worker._keyword_pass()  # noqa: SLF001
    await worker._embed_pass()  # noqa: SLF001

    assert len(await store.item_documents(1)) == first


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


# ---------------------------------------------------------------------------
# The deterministic event backfill (P5 review, finding 8)
# ---------------------------------------------------------------------------


async def test_event_backfill_gives_an_already_distilled_corpus_its_events(store):
    """The distillation stage claims only items that are NOT yet distilled, so
    a corpus imported before the event tables existed would never reach event
    derivation at all. The backfill closes that without one model call."""
    from jarvis.ultrawiki.pipeline import EVENTS_BACKFILL_CURSOR
    from jarvis.ultrawiki.types import DocType

    await store.upsert_items("src1", [make_item(1)])
    item = await store.get_item_by_external_id("src1", "ext-0001")
    item_id = int(item["id"])
    await store.mark_stage_done(item_id, ItemState.KEYWORD_INDEXED)
    await store.mark_stage_done(item_id, ItemState.EMBEDDED)
    await store.mark_stage_done(item_id, ItemState.DISTILLED)
    await store.add_document(
        item_id,
        DocType.SUMMARY,
        "summary text",
        distill_json=json.dumps(
            {"question": "When did it start?", "summary": "It began on 2026-02-02."}
        ),
        distill_version=1,
    )
    assert await store.list_events() == []

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: FakeEmbeddingBackend(),
        distill_fn=distill_never,  # the whole point: no model is involved
        distill_ready_fn=DISTILL_READY,
    )
    assert await worker._events_backfill_pass() == 1
    events = await store.list_events()
    assert len(events) == 1
    assert events[0]["occurred_at"].startswith("2026-02-02")
    # ... and the mentioned entities did NOT become people.
    assert await store.list_people(kind=None, limit=50) == []

    # The lane terminates: a second pass drains, a third costs one meta read.
    assert await worker._events_backfill_pass() == 0
    assert worker._events_backfill_open is False
    from jarvis.ultrawiki.events import EVENT_VERSION

    assert await store.get_meta(f"{EVENTS_BACKFILL_CURSOR}_v{EVENT_VERSION}") == "-1"


async def test_event_backfill_resumes_from_its_cursor(store):
    """It walks the corpus once, by id, and survives a restart mid-corpus."""
    from jarvis.ultrawiki.events import EVENT_VERSION
    from jarvis.ultrawiki.pipeline import EVENTS_BACKFILL_CURSOR
    from jarvis.ultrawiki.types import DocType

    await store.upsert_items("src1", [make_item(1), make_item(2)])
    ids = []
    for index in (1, 2):
        row = await store.get_item_by_external_id("src1", f"ext-{index:04d}")
        item_id = int(row["id"])
        ids.append(item_id)
        await store.add_document(
            item_id, DocType.SUMMARY, "t", distill_json='{"summary": "on 2026-02-02"}'
        )
    key = f"{EVENTS_BACKFILL_CURSOR}_v{EVENT_VERSION}"
    await store.set_meta(key, str(ids[0]))

    worker = PipelineWorker(
        store,
        make_cfg(),
        embedding_backend_factory=lambda: FakeEmbeddingBackend(),
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    assert await worker._events_backfill_pass() == 1
    assert {event["item_id"] for event in await store.list_events()} == {ids[1]}
    assert await store.get_meta(key) == str(ids[1])


async def test_event_backfill_is_silent_on_a_store_that_cannot_do_it(store):
    """A third-party store or a test fake simply has no backfill — and no error."""

    class Bare:
        pass

    worker = PipelineWorker(
        Bare(),
        make_cfg(),
        embedding_backend_factory=lambda: None,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )
    assert await worker._events_backfill_pass() == 0
    assert worker._events_backfill_open is False
