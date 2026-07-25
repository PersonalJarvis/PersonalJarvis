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
    assert sync_state["backfill_checkpoint"] == "f-0449"
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
