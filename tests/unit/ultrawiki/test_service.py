"""UltraWikiService unit tests — tmp SQLite store, fake connectors, offline.

Covers: activation registering PENDING sources without pulling a byte; the
consent + enabled refusal gates on start_sync; a full approved sync with
chunked upserts, per-chunk checkpoints, cursor advance, and a terminal job
snapshot; mid-stream cancellation; and the cancel-then-close shutdown leaving
no stray tasks. No network, no credentials, no models.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.ultrawiki.connectors as connectors_mod
import jarvis.ultrawiki.service as service_mod
from jarvis.ultrawiki.service import (
    JOB_TERMINAL_STATUSES,
    SyncAlreadyRunningError,
    UltraWikiService,
    clear_jobs,
)
from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorCapabilities,
    IncrementalMode,
    RawItem,
)


def make_cfg(tmp_path: Path, *, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            enabled=enabled,
            db_backend="sqlite",
            embedding_provider="",
            embedding_model="",
            distill_provider="",
            distill_model="",
            rerank_provider="",
            ollama_endpoint="",
        ),
        memory=SimpleNamespace(data_dir=str(tmp_path)),
        brain=SimpleNamespace(primary=""),
    )


def make_items(count: int) -> list[RawItem]:
    return [
        RawItem(
            external_id=f"f-{index:04d}",
            body=f"fake item body {index}",
            permalink=f"fake://item/{index}",
            timestamp_utc=f"2026-01-01T00:{index % 60:02d}:00Z",
            title=f"Fake {index}",
            metadata={"mtime_ns": 1000 + index},
        )
        for index in range(count)
    ]


class FakeConnector:
    """Cursor-capable fake following the file-walker connector conventions."""

    id = "fake-conn"
    label = "Fake Connector"
    auth = AuthKind.NONE
    capabilities = ConnectorCapabilities(
        backfill=True, incremental=IncrementalMode.CURSOR, deletes=True
    )

    def __init__(self, items: list[RawItem], calls: list[tuple[str, Any]]) -> None:
        self._items = items
        self.calls = calls

    async def backfill(self, ctx: Any, checkpoint: str | None = None):
        self.calls.append(("backfill", checkpoint))
        for item in self._items:
            if checkpoint and item.external_id <= checkpoint:
                continue
            yield item

    async def incremental(self, ctx: Any, cursor: str | None = None):
        self.calls.append(("incremental", cursor))
        threshold = int(cursor) if cursor else 0
        for item in self._items:
            if int(item.metadata.get("mtime_ns", 0)) > threshold:
                yield item


class ScheduledFakeConnector(FakeConnector):
    """Fake with both scheduler lanes enabled."""

    capabilities = ConnectorCapabilities(
        backfill=True,
        incremental=IncrementalMode.CURSOR,
        deletes=True,
        refresh_interval_s=60.0,
        reconcile_interval_s=86_400.0,
    )


class ExportFileConnector:
    """Backfill-only fake (IncrementalMode.NONE) — the export-file shape."""

    id = "export-conn"
    label = "Export File"
    auth = AuthKind.EXPORT_FILE
    capabilities = ConnectorCapabilities(
        backfill=True, incremental=IncrementalMode.NONE, deletes=False
    )

    def __init__(self, items: list[RawItem], calls: list[tuple[str, Any]]) -> None:
        self._items = items
        self.calls = calls

    async def backfill(self, ctx: Any, checkpoint: str | None = None):
        self.calls.append(("backfill", checkpoint))
        for item in self._items:
            if checkpoint and item.external_id <= checkpoint:
                continue
            yield item

    async def incremental(self, ctx: Any, cursor: str | None = None):
        raise AssertionError("a NONE-mode connector is never asked for increments")
        yield  # pragma: no cover — makes this an async generator


class BlockingConnector:
    """Yields its items, then blocks until the test releases (or cancels) it."""

    id = "blocking-conn"
    label = "Blocking Connector"
    auth = AuthKind.NONE
    capabilities = ConnectorCapabilities(
        backfill=True, incremental=IncrementalMode.NONE, deletes=False
    )

    def __init__(
        self, items: list[RawItem], gate: asyncio.Event, reached: asyncio.Event
    ) -> None:
        self._items = items
        self._gate = gate
        self._reached = reached

    async def backfill(self, ctx: Any, checkpoint: str | None = None):
        for item in self._items:
            yield item
        self._reached.set()
        await self._gate.wait()

    async def incremental(self, ctx: Any, cursor: str | None = None):
        return
        yield  # pragma: no cover — makes this an async generator


@pytest.fixture
async def service(tmp_path: Path):
    clear_jobs()
    svc = UltraWikiService(make_cfg(tmp_path))
    yield svc
    await svc.shutdown()
    clear_jobs()


async def wait_for_job(
    svc: UltraWikiService, job_id: str, *, deadline_s: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        snap = svc.job_snapshot(job_id)
        if snap is not None and snap["status"] in JOB_TERMINAL_STATUSES:
            return snap
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish: {svc.job_snapshot(job_id)}")


def patch_connectors(monkeypatch: pytest.MonkeyPatch, registry: dict[str, Any]) -> None:
    monkeypatch.setattr(connectors_mod, "discover_connectors", lambda: registry)


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


async def test_activate_registers_pending_sources_and_pulls_nothing(service):
    result = await service.activate({})

    assert result["default_area"]
    assert sorted(result["sources_created"]) == [
        "jarvis-conversations",
        "normal-wiki",
    ]
    status = await service.status()
    assert status["started"] is True
    assert status["backend"]["in_use"] == "sqlite"
    by_id = {src["id"]: src for src in status["sources"]}
    assert by_id["normal-wiki"]["consent"] == "pending"
    assert by_id["jarvis-conversations"]["consent"] == "pending"
    # Nothing was pulled: zero items, zero sync jobs.
    assert status["counts"]["total"] == 0
    assert service.list_jobs() == []

    # Activation is idempotent — a second run creates nothing new.
    again = await service.activate({})
    assert again["sources_created"] == []
    assert sorted(again["sources_existing"]) == [
        "jarvis-conversations",
        "normal-wiki",
    ]


async def test_activate_seeds_extra_areas(service):
    result = await service.activate({"areas": ["Work Stuff"]})
    assert "work-stuff" in result["areas_created"]
    store = service._require_store()
    area_ids = {area["id"] for area in await store.list_areas()}
    assert "work-stuff" in area_ids


# ---------------------------------------------------------------------------
# Consent + enabled gates
# ---------------------------------------------------------------------------


async def test_start_sync_refuses_unapproved_source(service):
    await service.activate({})
    with pytest.raises(ValueError, match="not approved"):
        await service.start_sync("normal-wiki")


async def test_start_sync_refuses_when_disabled(tmp_path):
    svc = UltraWikiService(make_cfg(tmp_path, enabled=False))
    try:
        with pytest.raises(ValueError, match="disabled"):
            await svc.start_sync("anything")
    finally:
        await svc.shutdown()


async def test_add_source_rejects_unknown_connector(service, monkeypatch):
    patch_connectors(monkeypatch, {})
    with pytest.raises(ValueError, match="unknown connector"):
        await service.add_source("no-such-conn", "Nope")


# ---------------------------------------------------------------------------
# Approved sync: chunked ingest, checkpoints, cursor advance, job snapshot
# ---------------------------------------------------------------------------


async def test_approved_sync_ingests_with_checkpoints_and_cursor(service, monkeypatch):
    items = make_items(450)  # 3 chunks: 200 + 200 + 50
    calls: list[tuple[str, Any]] = []
    patch_connectors(
        monkeypatch, {"fake-conn": lambda: FakeConnector(items, calls)}
    )

    source = await service.add_source("fake-conn", "Fake Source")
    source_id = source["id"]
    approved = await service.approve_source(source_id, auto_sync=False)
    assert approved["source"]["consent"] == "approved"
    assert approved["job_id"] is None  # auto_sync=False imports nothing

    job_id = await service.start_sync(source_id)
    snap = await wait_for_job(service, job_id)

    assert snap["status"] == "done"
    assert snap["mode"] == "backfill"
    assert snap["new"] == 450
    assert snap["chunks"] == 3
    assert snap["tombstoned"] == 0
    assert snap["error"] == ""

    store = service._require_store()
    sync_state = await store.get_sync_state(source_id)
    assert sync_state is not None
    assert sync_state["backfill_complete_at"]
    # The resume checkpoint is CLEARED once the walk completes: kept, it points
    # at the last item of a finished backfill, so the next backfill would
    # resume at the end and yield nothing.
    assert sync_state["backfill_checkpoint"] is None
    assert sync_state["cursor"] == str(1000 + 449)  # max mtime_ns convention
    refreshed = await store.get_source(source_id)
    assert refreshed["last_sync_at"]
    assert refreshed["last_error"] is None
    counts = await store.counts_for_source(source_id)
    assert counts.total == 450

    # A second sync switches to incremental and resumes from the cursor.
    job2_id = await service.start_sync(source_id)
    snap2 = await wait_for_job(service, job2_id)
    assert snap2["status"] == "done"
    assert snap2["mode"] == "incremental"
    assert snap2["new"] == 0
    assert ("incremental", "1449") in calls


async def test_freshness_scheduler_runs_incremental_then_full_reconcile(
    service, monkeypatch
):
    """Approved sources stay fresh without pressing Import again."""
    calls: list[tuple[str, Any]] = []
    patch_connectors(
        monkeypatch,
        {"scheduled": lambda: ScheduledFakeConnector(make_items(3), calls)},
    )
    source = await service.add_source("scheduled", "Scheduled Source")
    await service.approve_source(source["id"], auto_sync=False)
    await wait_for_job(
        service, await service.start_sync(source["id"], full=True)
    )

    assert await service._sync_due_sources(now=datetime.now(UTC)) == []  # noqa: SLF001

    incremental_at = datetime.now(UTC) + timedelta(minutes=2)
    [incremental_id] = await service._sync_due_sources(  # noqa: SLF001
        now=incremental_at
    )
    incremental = await wait_for_job(service, incremental_id)
    assert incremental["mode"] == "incremental"

    reconcile_at = datetime.now(UTC) + timedelta(days=2)
    [reconcile_id] = await service._sync_due_sources(  # noqa: SLF001
        now=reconcile_at
    )
    reconcile = await wait_for_job(service, reconcile_id)
    assert reconcile["mode"] == "backfill"
    assert [kind for kind, _cursor in calls] == [
        "backfill",
        "incremental",
        "backfill",
    ]


async def test_manual_export_source_is_not_polled(service, monkeypatch):
    calls: list[tuple[str, Any]] = []
    patch_connectors(
        monkeypatch,
        {"export-conn": lambda: ExportFileConnector(make_items(2), calls)},
    )
    source = await service.add_source("export-conn", "Export File")
    await service.approve_source(source["id"], auto_sync=False)

    assert (
        await service._sync_due_sources(  # noqa: SLF001
            now=datetime.now(UTC) + timedelta(days=30)
        )
        == []
    )
    assert calls == []


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def test_cancel_job_mid_stream_stops_the_sync(service, monkeypatch):
    gate = asyncio.Event()
    reached = asyncio.Event()
    items = make_items(250)  # one full chunk lands, 50 stay buffered
    patch_connectors(
        monkeypatch,
        {"blocking-conn": lambda: BlockingConnector(items, gate, reached)},
    )

    source = await service.add_source("blocking-conn", "Blocked Source")
    await service.approve_source(source["id"], auto_sync=False)
    job_id = await service.start_sync(source["id"])

    await asyncio.wait_for(reached.wait(), timeout=5.0)
    assert service.cancel_job(job_id) is True
    snap = await wait_for_job(service, job_id)

    assert snap["status"] == "cancelled"
    assert snap["new"] == 200  # the flushed chunk survived, the buffer did not
    store = service._require_store()
    counts = await store.counts_for_source(source["id"])
    assert counts.total == 200
    # Cancelling a terminal job is a no-op.
    assert service.cancel_job(job_id) is False


# ---------------------------------------------------------------------------
# Shutdown discipline
# ---------------------------------------------------------------------------


async def test_shutdown_leaves_no_stray_tasks(service, monkeypatch):
    gate = asyncio.Event()
    reached = asyncio.Event()
    patch_connectors(
        monkeypatch,
        {"blocking-conn": lambda: BlockingConnector(make_items(10), gate, reached)},
    )
    await service.activate({})
    pipeline_task = service._pipeline_task
    freshness_task = service._freshness_task
    assert pipeline_task is not None and not pipeline_task.done()
    assert freshness_task is not None and not freshness_task.done()

    source = await service.add_source("blocking-conn", "Blocked Source")
    await service.approve_source(source["id"], auto_sync=False)
    job_id = await service.start_sync(source["id"])
    await asyncio.wait_for(reached.wait(), timeout=5.0)
    sync_task = service._sync_tasks[job_id]

    await service.shutdown()

    assert pipeline_task.done()
    assert freshness_task.done()
    assert sync_task.done()
    assert service._pipeline_task is None
    assert service._freshness_task is None
    assert service._sync_tasks == {}
    assert service._store is None
    snap = service.job_snapshot(job_id)
    assert snap is not None and snap["status"] == "cancelled"


async def test_ensure_started_is_idempotent(service):
    await service.ensure_started()
    first_store = service._store
    first_task = service._pipeline_task
    await service.ensure_started()
    assert service._store is first_store
    assert service._pipeline_task is first_task


# ---------------------------------------------------------------------------
# Full refresh + the checkpoint that used to freeze export-file sources
# ---------------------------------------------------------------------------


async def test_export_style_source_reingests_on_every_backfill(service, monkeypatch):
    """A connector that cannot run incrementally must not freeze after run 1.

    ``IncrementalMode.NONE`` means every sync is a backfill. While the resume
    checkpoint survived a completed walk, run 2 resumed at the END of run 1 and
    yielded nothing — forever.
    """
    calls: list[tuple[str, Any]] = []
    patch_connectors(
        monkeypatch,
        {"export-conn": lambda: ExportFileConnector(make_items(5), calls)},
    )
    source = await service.add_source("export-conn", "Export File")
    await service.approve_source(source["id"], auto_sync=False)

    first = await wait_for_job(service, await service.start_sync(source["id"]))
    assert (first["mode"], first["new"]) == ("backfill", 5)

    second = await wait_for_job(service, await service.start_sync(source["id"]))
    assert second["mode"] == "backfill"
    # Nothing NEW (the items are unchanged), but every item was seen again —
    # that is what keeps content edits and deletions detectable.
    assert second["unchanged"] == 5
    assert [checkpoint for _mode, checkpoint in calls] == [None, None]


async def test_full_refresh_resets_the_cursor_and_reconciles_deletes(
    service, monkeypatch
):
    items = make_items(3)
    calls: list[tuple[str, Any]] = []
    connector = FakeConnector(items, calls)
    patch_connectors(monkeypatch, {"fake-conn": lambda: connector})

    source = await service.add_source("fake-conn", "Fake Source")
    source_id = source["id"]
    await service.approve_source(source_id, auto_sync=False)
    await wait_for_job(service, await service.start_sync(source_id))
    store = service._require_store()
    assert (await store.counts_for_source(source_id)).total == 3

    # The source loses an item. An INCREMENTAL run cannot see a deletion:
    # a walk only reports what still exists.
    connector._items = items[:2]
    incremental = await wait_for_job(service, await service.start_sync(source_id))
    assert incremental["mode"] == "incremental"
    assert incremental["tombstoned"] == 0
    assert (await store.counts_for_source(source_id)).total == 3

    # The full refresh re-reads everything and reconciles the difference.
    full = await wait_for_job(
        service, await service.start_sync(source_id, full=True)
    )
    assert full["mode"] == "backfill"
    assert full["tombstoned"] == 1
    assert (await store.counts_for_source(source_id)).total == 2
    sync_state = await store.get_sync_state(source_id)
    assert sync_state["backfill_checkpoint"] is None


async def test_full_refresh_records_completion_after_delete_reconciliation(
    service, monkeypatch
):
    items = make_items(1)
    connector = FakeConnector(items, [])
    patch_connectors(monkeypatch, {"fake-conn": lambda: connector})

    source = await service.add_source("fake-conn", "Fake Source")
    await service.approve_source(source["id"], auto_sync=False)
    await wait_for_job(service, await service.start_sync(source["id"]))

    store = service._require_store()
    original_reconcile = store.reconcile_deletes
    order: list[str] = []

    async def traced_reconcile(source_id: str, yielded_ids: set[str]) -> int:
        order.append("reconcile")
        return await original_reconcile(source_id, yielded_ids)

    completed_at = "2026-07-30T16:33:34Z"

    def completion_clock() -> str:
        order.append("clock")
        return completed_at

    monkeypatch.setattr(store, "reconcile_deletes", traced_reconcile)
    monkeypatch.setattr(service_mod, "_iso_now", completion_clock)
    connector._items = []

    full = await wait_for_job(
        service, await service.start_sync(source["id"], full=True)
    )

    assert full["status"] == "done"
    assert full["tombstoned"] == 1
    assert order == ["reconcile", "clock"]
    sync_state = await store.get_sync_state(source["id"])
    assert sync_state["backfill_complete_at"] == completed_at
    assert sync_state["last_success_at"] == completed_at


# ---------------------------------------------------------------------------
# One sync per source at a time
# ---------------------------------------------------------------------------


async def test_second_sync_of_one_source_is_refused_with_the_active_job(
    service, monkeypatch
):
    gate = asyncio.Event()
    reached = asyncio.Event()
    patch_connectors(
        monkeypatch,
        {"blocking-conn": lambda: BlockingConnector(make_items(3), gate, reached)},
    )
    source = await service.add_source("blocking-conn", "Blocked Source")
    await service.approve_source(source["id"], auto_sync=False)
    job_id = await service.start_sync(source["id"])
    await asyncio.wait_for(reached.wait(), timeout=5.0)

    with pytest.raises(SyncAlreadyRunningError) as excinfo:
        await service.start_sync(source["id"])
    assert excinfo.value.job_id == job_id
    assert excinfo.value.source_id == source["id"]

    # Once it finishes, a new sync starts normally.
    service.cancel_job(job_id)
    await wait_for_job(service, job_id)
    assert await service.start_sync(source["id"]) != job_id


# ---------------------------------------------------------------------------
# Honest pipeline state (the "Pipeline running · Captured 0" report)
# ---------------------------------------------------------------------------


async def test_pipeline_state_waits_for_sources_before_anything_is_approved(service):
    await service.activate({})
    status = await service.status()
    assert status["pipeline"]["state"] == "waiting_for_sources"
    reason = status["pipeline"]["reason"]
    assert "approve" in reason.lower()
    # The worker loop IS alive — that is exactly why "running" alone lied.
    assert status["pipeline"]["running"] is True


async def test_pipeline_state_is_idle_once_an_approved_source_is_drained(
    service, monkeypatch
):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector([], [])})
    source = await service.add_source("fake-conn", "Empty Source")
    await service.approve_source(source["id"], auto_sync=False)
    status = await service.status()
    assert status["pipeline"]["state"] == "idle"
    assert "processed" in status["pipeline"]


async def test_pipeline_state_is_paused_when_the_embedding_slot_blocks(
    service, monkeypatch
):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(2), [])})
    source = await service.add_source("fake-conn", "Fake Source")
    await service.approve_source(source["id"], auto_sync=False)
    await wait_for_job(service, await service.start_sync(source["id"]))
    # Drive the keyword stage so the backlog sits at the embedding gate.
    store = service._require_store()
    for item in await store.claim_batch("keyword_indexed", limit=10):
        await store.mark_stage_done(
            item["id"],
            "keyword_indexed",
            fts_title=item["title"],
            fts_body=item["body_raw"],
        )

    status = await service.status()
    assert status["pipeline"]["state"] == "paused"
    assert "embedding" in status["pipeline"]["reason"]
    assert status["slots"]["embedding"]["ready"] is False


async def test_a_refusing_embedding_provider_is_reported_even_while_keywords_flow(
    service, monkeypatch
):
    """The forensic case of 2026-07-27, end to end.

    An OpenAI key with no credit answered ``HTTP 429 (insufficient_quota)``
    for fifteen hours. The keyword lane needs no model, so it kept ticking —
    and because it did, the strip reported "processing", the checklist said
    "you do not have to wait", and the problem list said "Nothing needs your
    attention" over a corpus where not one item had gained a summary since the
    previous evening.

    The slot probe cannot see this by contract: it is a CREDENTIAL check
    (AP-21), and the key exists. Only the worker holds the provider's actual
    answer, so the test that matters is that the answer reaches the payload.
    """
    from jarvis.ultrawiki.embeddings import EmbeddingError
    from jarvis.ultrawiki.health import build_health

    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(6), [])})
    source = await service.add_source("fake-conn", "Fake Source")
    await service.approve_source(source["id"], auto_sync=False)
    await wait_for_job(service, await service.start_sync(source["id"]))

    # Half the corpus past the keyword gate, half still behind it — exactly
    # the live shape, where the free stage still has work to show.
    store = service._require_store()
    for item in await store.claim_batch("keyword_indexed", limit=3):
        await store.mark_stage_done(
            item["id"],
            "keyword_indexed",
            fts_title=item["title"],
            fts_body=item["body_raw"],
        )

    # A credential-ready slot, so nothing but the worker's knowledge can
    # produce the honest verdict.
    service._cfg.ultrawiki.embedding_provider = "fake-ready"
    monkeypatch.setattr(
        service,
        "_embedding_slot_status",
        lambda: {"provider": "fake-ready", "model": "m", "ready": True, "reason": ""},
    )
    service._pipeline._begin_embed_cooldown(
        EmbeddingError("fake: embedding request failed with HTTP 429 (insufficient_quota)")
    )

    status = await service.status()

    assert status["pipeline"]["state"] == "paused", "a refused lane is not progress"
    assert status["pipeline"]["blocked"] is True
    assert status["pipeline"]["blocked_needs_attention"] is True
    reason = status["pipeline"]["reason"]
    assert "insufficient_quota" in reason
    assert "will not clear" in reason, "it has to say waiting is not the fix"
    assert "Keyword indexing" in reason, "and what IS still moving"
    # The slot stops claiming to be usable, whatever the credential says.
    assert status["slots"]["embedding"]["ready"] is False
    assert status["slots"]["embedding"]["needs_attention"] is True

    # ...and the row that used to say "you do not have to wait" now asks.
    processing = next(
        c for c in build_health(status)["checks"] if c["id"] == "processing"
    )
    assert processing["state"] == "attention"
    assert "insufficient_quota" in processing["detail"]


async def test_pipeline_state_is_processing_with_a_claimable_backlog(
    service, monkeypatch
):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(4), [])})
    source = await service.add_source("fake-conn", "Fake Source")
    await service.approve_source(source["id"], auto_sync=False)
    await wait_for_job(service, await service.start_sync(source["id"]))

    status = await service.status()
    assert status["pipeline"]["state"] == "processing"
    assert "4" in status["pipeline"]["reason"]


async def test_status_reports_the_credential_behind_a_ready_slot(service, monkeypatch):
    """Provenance names the credential path, never the key itself."""
    import jarvis.ultrawiki.embeddings as embeddings_mod

    service._cfg.ultrawiki.embedding_provider = "gemini"
    monkeypatch.setattr(
        embeddings_mod,
        "available_backends",
        lambda cfg: [
            {"name": "gemini", "ready": True, "reason": "", "default_model": "m"}
        ],
    )
    monkeypatch.setattr(
        embeddings_mod,
        "credential_source",
        lambda name, cfg: "your saved Gemini API key (gemini_api_key)",
    )
    status = await service.status()
    assert status["slots"]["embedding"]["via"] == (
        "your saved Gemini API key (gemini_api_key)"
    )
    assert "gemini_api_key" in status["slots"]["embedding"]["via"]


# ---------------------------------------------------------------------------
# Dead-letter recovery
# ---------------------------------------------------------------------------


async def test_requeue_failed_is_exposed_and_scoped(service, monkeypatch):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(2), [])})
    source = await service.add_source("fake-conn", "Fake Source")
    await service.approve_source(source["id"], auto_sync=False)
    await wait_for_job(service, await service.start_sync(source["id"]))
    store = service._require_store()
    for item in await store.claim_batch("keyword_indexed", limit=10):
        await store.mark_failed(item["id"], "the distill provider was dead")
    assert (await store.counts()).failed == 2

    assert await service.requeue_failed(source["id"]) == 2
    assert (await store.counts()).failed == 0
    with pytest.raises(ValueError, match="unknown source"):
        await service.requeue_failed("no-such-source")


# ---------------------------------------------------------------------------
# A failing Postgres store degrades WITHOUT leaking the connection string
# ---------------------------------------------------------------------------


async def test_postgres_degradation_never_leaks_the_password(tmp_path, monkeypatch):
    import jarvis.ultrawiki.store as store_mod

    dsn = "postgresql://jarvis:hunter2@db.internal:5432/uw"

    class ExplodingStore:
        def __init__(self, conn_str: str) -> None:
            self._conn_str = conn_str

        async def open(self) -> None:
            raise OSError(f'connection to "{self._conn_str}" was refused')

    monkeypatch.setattr(store_mod, "PostgresStore", ExplodingStore)
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda name: dsn)
    cfg = make_cfg(tmp_path)
    cfg.ultrawiki.db_backend = "postgres"
    svc = UltraWikiService(cfg)
    try:
        status = await svc.status()
        await svc.ensure_started()
        status = await svc.status()
        degradations = " ".join(status["degradations"])
        assert "hunter2" not in degradations
        assert dsn not in degradations
        assert "***" in degradations
        # The store still opened — on SQLite, honestly reported.
        assert status["backend"]["in_use"] == "sqlite"
    finally:
        await svc.shutdown()


# ---------------------------------------------------------------------------
# Approve = import everything
# ---------------------------------------------------------------------------


async def test_approve_starts_the_full_import_immediately(service, monkeypatch):
    """Approving IS the import — the click that used to only flip a flag."""
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(6), [])})
    source = await service.add_source("fake-conn", "Fake Source")

    approved = await service.approve_source(source["id"])

    assert approved["source"]["consent"] == "approved"
    assert approved["auto_sync"] is True
    assert approved["job_id"]
    assert "COPIED" in approved["detail"]  # nothing is removed at the origin
    snap = await wait_for_job(service, approved["job_id"])
    assert (snap["status"], snap["new"]) == ("done", 6)
    # A full refresh, so deletions are detectable from the very first import.
    assert snap["mode"] == "backfill"
    store = service._require_store()
    assert (await store.counts_for_source(source["id"])).total == 6


async def test_approve_without_auto_sync_only_grants_consent(service, monkeypatch):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(3), [])})
    source = await service.add_source("fake-conn", "Fake Source")

    approved = await service.approve_source(source["id"], auto_sync=False)

    assert approved["source"]["consent"] == "approved"
    assert approved["job_id"] is None
    assert service.list_jobs() == []
    store = service._require_store()
    assert (await store.counts_for_source(source["id"])).total == 0


async def test_approve_keeps_consent_when_no_import_can_start(service, monkeypatch):
    """A refused import must never cost the user their approval."""
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector([], [])})
    source = await service.add_source("fake-conn", "Fake Source")
    service._cfg.ultrawiki.enabled = False  # start_sync refuses while off

    approved = await service.approve_source(source["id"])

    assert approved["source"]["consent"] == "approved"
    assert approved["job_id"] is None
    assert "Consent was granted" in approved["detail"]
    assert "disabled" in approved["detail"]


# ---------------------------------------------------------------------------
# Per-source progress: the live job and the restart-proof last outcome
# ---------------------------------------------------------------------------


async def test_source_summary_carries_the_last_outcome_after_a_sync(
    service, monkeypatch
):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(4), [])})
    source = await service.add_source("fake-conn", "Fake Source")
    approved = await service.approve_source(source["id"])
    await wait_for_job(service, approved["job_id"])

    status = await service.status()
    row = next(s for s in status["sources"] if s["id"] == source["id"])

    assert row["active_job"] is None  # the run finished
    outcome = row["last_outcome"]
    assert outcome["status"] == "done"
    assert outcome["mode"] == "backfill"
    assert outcome["new"] == 4
    assert outcome["finished_at"]
    assert row["last_notice"] is None


async def test_source_summary_reports_the_running_job_with_its_phase(
    service, monkeypatch
):
    gate = asyncio.Event()
    reached = asyncio.Event()
    patch_connectors(
        monkeypatch,
        {"blocking-conn": lambda: BlockingConnector(make_items(250), gate, reached)},
    )
    source = await service.add_source("blocking-conn", "Blocked Source")
    approved = await service.approve_source(source["id"])
    await asyncio.wait_for(reached.wait(), timeout=5.0)

    status = await service.status()
    row = next(s for s in status["sources"] if s["id"] == source["id"])
    active = row["active_job"]

    assert active["job_id"] == approved["job_id"]
    assert active["status"] == "running"
    assert active["phase"] == "importing"
    # Items PULLED so far — the ticking number the progress bar shows.
    assert active["items"] == 200
    assert row["last_outcome"] is None  # nothing has finished yet

    gate.set()
    await wait_for_job(service, approved["job_id"])


async def test_a_failed_sync_records_an_honest_outcome(service, monkeypatch):
    class ExplodingConnector:
        id = "boom-conn"
        label = "Exploding Connector"
        auth = AuthKind.NONE
        capabilities = ConnectorCapabilities(
            backfill=True, incremental=IncrementalMode.NONE, deletes=False
        )

        async def backfill(self, ctx, checkpoint=None):
            raise RuntimeError("the export file is corrupt")
            yield  # pragma: no cover — makes this an async generator

        async def incremental(self, ctx, cursor=None):
            return
            yield  # pragma: no cover — makes this an async generator

    patch_connectors(monkeypatch, {"boom-conn": ExplodingConnector})
    source = await service.add_source("boom-conn", "Boom")
    approved = await service.approve_source(source["id"])
    await wait_for_job(service, approved["job_id"])

    row = next(
        s for s in (await service.status())["sources"] if s["id"] == source["id"]
    )
    assert row["last_outcome"]["status"] == "failed"
    assert "corrupt" in row["last_error"]


# ---------------------------------------------------------------------------
# Plugin bridge: real names, and the honest "no pull adapter yet" notice
# ---------------------------------------------------------------------------


def patch_bridge_candidates(
    monkeypatch: pytest.MonkeyPatch, candidates: list[dict[str, str]]
) -> None:
    from jarvis.ultrawiki.connectors import plugin_bridge

    monkeypatch.setattr(plugin_bridge, "list_candidates", lambda: list(candidates))


async def test_bridge_source_is_named_after_the_integration(service, monkeypatch):
    """A list of identical 'Connected Integrations' cards told nobody anything."""
    patch_bridge_candidates(
        monkeypatch,
        [{"id": "plugin:github", "kind": "plugin", "label": "GitHub", "detail": ""}],
    )

    source = await service.add_source(
        "plugin-bridge", "", config={"integration_id": "plugin:github"}
    )

    assert source["label"] == "GitHub"


async def test_a_real_bridge_label_is_never_overwritten(service, monkeypatch):
    patch_bridge_candidates(
        monkeypatch,
        [{"id": "plugin:github", "kind": "plugin", "label": "GitHub", "detail": ""}],
    )

    source = await service.add_source(
        "plugin-bridge", "Work repos", config={"integration_id": "plugin:github"}
    )

    assert source["label"] == "Work repos"


async def test_a_bridge_sync_without_a_pull_adapter_records_an_honest_notice(
    service, monkeypatch
):
    """Zero imported items used to leave nothing but a log line."""
    from jarvis.ultrawiki.service import BRIDGE_ADAPTER_PENDING_NOTICE

    patch_bridge_candidates(
        monkeypatch,
        [{"id": "mcp:filesystem", "kind": "mcp", "label": "Filesystem", "detail": ""}],
    )
    source = await service.add_source(
        "plugin-bridge", "", config={"integration_id": "mcp:filesystem"}
    )
    approved = await service.approve_source(source["id"])
    await wait_for_job(service, approved["job_id"])

    row = next(
        s for s in (await service.status())["sources"] if s["id"] == source["id"]
    )
    assert row["last_notice"] == BRIDGE_ADAPTER_PENDING_NOTICE
    # It is a NOTICE, not an error: the sync itself was healthy.
    assert row["last_error"] is None
    assert row["last_outcome"]["status"] == "done"
    assert row["integration_id"] == "mcp:filesystem"


async def test_the_notice_disappears_once_the_adapter_ships(service, monkeypatch):
    from jarvis.ultrawiki.connectors import plugin_bridge

    patch_bridge_candidates(
        monkeypatch,
        [{"id": "mcp:filesystem", "kind": "mcp", "label": "Filesystem", "detail": ""}],
    )
    source = await service.add_source(
        "plugin-bridge", "", config={"integration_id": "mcp:filesystem"}
    )
    first = await service.approve_source(source["id"])
    await wait_for_job(service, first["job_id"])
    assert (await service._require_store().get_source(source["id"]))["last_notice"]

    async def adapter(ctx, checkpoint=None):
        yield RawItem(
            external_id="doc-1",
            body="the adapter shipped",
            permalink="mcp://filesystem/doc-1",
            timestamp_utc="2026-07-25T09:00:00Z",
            title="Doc 1",
        )

    plugin_bridge.register_pull_adapter("mcp:filesystem", adapter)
    try:
        await wait_for_job(service, await service.start_sync(source["id"]))
    finally:
        plugin_bridge.unregister_pull_adapter("mcp:filesystem")

    source_row = await service._require_store().get_source(source["id"])
    assert source_row["last_notice"] is None


async def test_generic_bridge_label_heals_on_start(tmp_path, monkeypatch):
    """A pre-fix bridge source stored under the generic card name gains the
    real integration label ("GitHub") on the next service start, persisted so
    the live-registry lookup never runs again for it."""
    from jarvis.ultrawiki.connectors import plugin_bridge

    monkeypatch.setattr(
        plugin_bridge,
        "list_candidates",
        lambda: [
            {
                "id": "plugin:github",
                "kind": "plugin",
                "label": "GitHub",
                "detail": "connected",
            }
        ],
    )
    svc = UltraWikiService(make_cfg(tmp_path))
    try:
        await svc.ensure_started()
        store = svc._require_store()
        await store.upsert_source(
            "plugin-bridge-legacy1",
            connector="plugin-bridge",
            label="Connected integration (plugins / MCP)",
            config={"integration_id": "plugin:github"},
        )
        # Heal runs on store OPEN — restart the service to trigger it again.
        await svc.shutdown()
        svc2 = UltraWikiService(make_cfg(tmp_path))
        try:
            await svc2.ensure_started()
            row = await svc2._require_store().get_source("plugin-bridge-legacy1")
            assert row is not None
            assert row["label"] == "GitHub"
            assert row["config"]["integration_id"] == "plugin:github"
        finally:
            await svc2.shutdown()
    finally:
        await svc.shutdown()
