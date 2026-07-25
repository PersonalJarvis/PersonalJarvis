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
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.ultrawiki.connectors as connectors_mod
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
    approved = await service.approve_source(source_id)
    assert approved["consent"] == "approved"

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
    await service.approve_source(source["id"])
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
    assert pipeline_task is not None and not pipeline_task.done()

    source = await service.add_source("blocking-conn", "Blocked Source")
    await service.approve_source(source["id"])
    job_id = await service.start_sync(source["id"])
    await asyncio.wait_for(reached.wait(), timeout=5.0)
    sync_task = service._sync_tasks[job_id]

    await service.shutdown()

    assert pipeline_task.done()
    assert sync_task.done()
    assert service._pipeline_task is None
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
    await service.approve_source(source["id"])

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
    await service.approve_source(source_id)
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
    await service.approve_source(source["id"])
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
    await service.approve_source(source["id"])
    status = await service.status()
    assert status["pipeline"]["state"] == "idle"
    assert "processed" in status["pipeline"]


async def test_pipeline_state_is_paused_when_the_embedding_slot_blocks(
    service, monkeypatch
):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(2), [])})
    source = await service.add_source("fake-conn", "Fake Source")
    await service.approve_source(source["id"])
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


async def test_pipeline_state_is_processing_with_a_claimable_backlog(
    service, monkeypatch
):
    patch_connectors(monkeypatch, {"fake-conn": lambda: FakeConnector(make_items(4), [])})
    source = await service.add_source("fake-conn", "Fake Source")
    await service.approve_source(source["id"])
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
    await service.approve_source(source["id"])
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
