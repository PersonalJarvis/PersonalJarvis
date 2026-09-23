"""Real isolated SQLite fixtures: capacity, fencing, rollback and cursor correctness."""

from __future__ import annotations

import asyncio
import threading

import aiosqlite
import pytest
from pydantic import ValidationError

from jarvis.society.mars.models import (
    CommandState,
    ExecutionReceipt,
    StationCommand,
    StationError,
)
from jarvis.society.mars.navigation_store import MarsNavigationStore
from jarvis.society.mars.store import MarsStore


@pytest.fixture
async def store(tmp_path):
    value = MarsStore(tmp_path / "mars.db")
    await value.open()
    try:
        yield value
    finally:
        await value.close()


@pytest.mark.parametrize("kind", ["station", "navigation"])
@pytest.mark.parametrize("cancel_at", ["begin", "body"])
async def test_cancellation_joins_rollback_before_unlock_and_connection_reuse(
    tmp_path, kind, cancel_at
):
    """Gate real SQLite worker statements, including cancellation during cleanup."""
    value = (MarsStore if kind == "station" else MarsNavigationStore)(tmp_path / "cancel.db")
    await value.open()
    transaction = value._transaction if kind == "station" else value.transaction
    connection = value._conn
    await connection.execute("CREATE TABLE cancellation_probe(value INTEGER)")
    loop = asyncio.get_running_loop()
    begin_seen, rollback_seen, body_seen = (asyncio.Event() for _ in range(3))
    begin_release, rollback_release = threading.Event(), threading.Event()
    callback_timeouts = []
    first_begin = True

    def trace(statement):
        nonlocal first_begin
        if statement == "BEGIN IMMEDIATE" and first_begin:
            first_begin = False
            loop.call_soon_threadsafe(begin_seen.set)
            if cancel_at == "begin" and not begin_release.wait(timeout=10):
                callback_timeouts.append("begin")
        if statement == "ROLLBACK":
            loop.call_soon_threadsafe(rollback_seen.set)
            if not rollback_release.wait(timeout=10):
                callback_timeouts.append("rollback")

    await connection.set_trace_callback(trace)

    async def write():
        async with transaction() as conn:
            await conn.execute("INSERT INTO cancellation_probe VALUES(1)")
            body_seen.set()
            await asyncio.Event().wait()

    async def successor():
        async with transaction() as conn:
            async with conn.execute("SELECT COUNT(*) FROM cancellation_probe") as cursor:
                assert (await cursor.fetchone())[0] == 0
            await conn.execute("INSERT INTO cancellation_probe VALUES(2)")

    writer = asyncio.create_task(write())
    next_writer = None
    try:
        await asyncio.wait_for((begin_seen if cancel_at == "begin" else body_seen).wait(), 2)
        writer.cancel()
        await asyncio.sleep(0)
        if cancel_at == "begin":
            # BEGIN is accepted by the worker but still blocked before execution.
            assert value._lock.locked() and not writer.done()
            assert not body_seen.is_set()
        begin_release.set()
        await asyncio.wait_for(rollback_seen.wait(), 2)
        writer.cancel()  # A stop deadline may cancel an owner already rolling back.
        await asyncio.sleep(0)
        assert value._lock.locked() and not writer.done()
        next_writer = asyncio.create_task(successor())
        await asyncio.sleep(0)
        assert not next_writer.done()
        rollback_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(writer, 2)
        await asyncio.wait_for(next_writer, 2)
        assert not connection.in_transaction
        async with connection.execute("SELECT value FROM cancellation_probe") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [2]
        assert callback_timeouts == []
    finally:
        begin_release.set()
        rollback_release.set()
        writer.cancel()
        if next_writer is not None:
            next_writer.cancel()
        await asyncio.gather(
            writer, *([next_writer] if next_writer else []), return_exceptions=True
        )
        await connection.set_trace_callback(None)
        await value.close()


def request(ident="request-1", text="Draft a short shift handover."):
    return StationCommand(request_id=ident, draft=text)


async def test_concurrent_duplicate_submit_has_one_identity_and_event(store):
    records = await asyncio.gather(*(store.submit("operator", request()) for _ in range(12)))
    assert len({row.command_id for row in records}) == 1
    assert (await store.snapshot()).seq == 1
    with pytest.raises(StationError, match="idempotency_payload_mismatch"):
        await store.submit("operator", request(text="Different content."))
    other = await store.submit("second-agent", request())
    assert other.command_id != records[0].command_id


async def test_exclusive_station_fifo_and_terminal_release(store):
    first = await store.submit("one", request("first"))
    second = await store.submit("two", request("second"))
    claims = await asyncio.gather(store.claim_next(), store.claim_next())
    owned = [record for record in claims if record is not None]
    assert len(owned) == 1 and owned[0].command_id == first.command_id
    assert (await store.get(second.command_id)).state is CommandState.QUEUED
    completed = await store.apply(
        first.command_id,
        owned[0].fence,
        ExecutionReceipt(
            state=CommandState.COMPLETED, task_ref="turn:one", result_ref="result:one"
        ),
    )
    assert completed.state is CommandState.COMPLETED
    assert (await store.snapshot()).lease is None
    next_claim = await store.claim_next()
    assert next_claim.command_id == second.command_id
    assert next_claim.fence > owned[0].fence
    with pytest.raises(StationError, match="stale_station_lease"):
        await store.apply(
            second.command_id,
            owned[0].fence,
            ExecutionReceipt(state=CommandState.FAILED),
        )


async def test_restart_fences_previous_owner_without_releasing_unknown_work(tmp_path):
    path = tmp_path / "restart.db"
    first = MarsStore(path)
    await first.open()
    queued = await first.submit("one", request())
    old = await first.claim_next()
    await first.close()
    second = MarsStore(path)
    await second.open()
    try:
        current = await second.current()
        assert current.command_id == queued.command_id
        assert current.state is CommandState.INTERRUPTED
        assert current.fence > old.fence
        assert await second.claim_next() is None
        with pytest.raises(StationError, match="stale_station_lease"):
            await second.apply(
                old.command_id, old.fence, ExecutionReceipt(state=CommandState.FAILED)
            )
    finally:
        await second.close()


async def test_expired_lease_requires_reconciliation_and_rejects_late_result(tmp_path):
    clock = [1000]
    store = MarsStore(tmp_path / "expiry.db", clock_ms=lambda: clock[0], lease_ms=100)
    await store.open()
    try:
        await store.submit("operator", request())
        old = await store.claim_next()
        clock[0] = 1101
        with pytest.raises(StationError, match="stale_station_lease"):
            await store.apply(
                old.command_id, old.fence, ExecutionReceipt(state=CommandState.FAILED)
            )
        current = await store.current()
        assert current.fence > old.fence
        assert current.state is CommandState.INTERRUPTED
        assert current.reason == "lease_expired"
        assert await store.claim_next() is None
    finally:
        await store.close()


async def test_second_store_cannot_take_live_owner_lock(tmp_path):
    first = MarsStore(tmp_path / "owner.db")
    second = MarsStore(tmp_path / "owner.db")
    await first.open()
    try:
        with pytest.raises(StationError, match="world_owned_by_another_process"):
            await second.open()
    finally:
        await first.close()
        await second.close()
    await second.open()
    await second.close()


async def test_event_failure_rolls_back_command_without_false_acknowledgement(store):
    async with store._transaction() as conn:
        await conn.execute(
            "CREATE TRIGGER reject_events BEFORE INSERT ON mars_events "
            "BEGIN SELECT RAISE(ABORT, 'test storage failure'); END"
        )
    with pytest.raises(aiosqlite.IntegrityError, match="test storage failure"):
        await store.submit("operator", request())
    assert (await store.snapshot()).commands == ()
    async with store._transaction() as conn:
        await conn.execute("DROP TRIGGER reject_events")
    result = await store.submit("operator", request())
    assert result.state is CommandState.QUEUED


async def test_snapshot_boundary_and_limited_delivered_cursor(store):
    first = await store.submit("operator", request())
    snapshot = await store.snapshot()
    assert snapshot.seq == 1
    await store.claim_next()
    await store.request_cancel("operator", first.command_id)
    batch = await store.events(snapshot.seq, limit=1)
    assert batch.next_cursor == 2 and batch.latest_seq == 3 and batch.has_more
    final = await store.events(batch.next_cursor, limit=1)
    assert final.next_cursor == 3 and not final.has_more
    assert [event.seq for event in batch.events + final.events] == [2, 3]
    # Retrying a page delivers identical immutable records for deterministic client dedupe.
    assert await store.events(snapshot.seq, limit=1) == batch


async def test_retention_gap_and_future_cursor_require_snapshot(tmp_path):
    store = MarsStore(tmp_path / "retained.db", retain_events=2)
    await store.open()
    try:
        command = await store.submit("operator", request())
        await store.claim_next()
        await store.request_cancel("operator", command.command_id)
        gap = await store.events(0)
        assert gap.resync_required and not gap.events and not gap.has_more
        assert (await store.events(999)).resync_required
        snapshot = await store.snapshot()
        assert not (await store.events(snapshot.seq)).resync_required
    finally:
        await store.close()


async def test_cancel_queued_does_not_consume_or_release_another_lease(store):
    first = await store.submit("one", request("first"))
    second = await store.submit("two", request("second"))
    await store.claim_next()
    with pytest.raises(StationError, match="command_not_found"):
        await store.request_cancel("one", second.command_id)
    canceled = await store.request_cancel("two", second.command_id)
    assert canceled.state is CommandState.CANCELED
    assert (await store.snapshot()).lease.command_id == first.command_id


async def test_queue_cap_preserves_idempotent_retrieval(tmp_path):
    store = MarsStore(tmp_path / "bounded.db", queue_limit=1)
    await store.open()
    try:
        first = await store.submit("one", request())
        assert (await store.submit("one", request())).command_id == first.command_id
        with pytest.raises(StationError, match="station_queue_full"):
            await store.submit("two", request())
    finally:
        await store.close()


async def test_private_draft_never_enters_snapshot_or_event_projection(store):
    private = "Only this private draft contains SECRET-CONTENT-SENTINEL."
    await store.submit("operator", request(text=private))
    assert "SECRET-CONTENT-SENTINEL" not in (await store.snapshot()).model_dump_json()
    assert "SECRET-CONTENT-SENTINEL" not in (await store.events(0)).model_dump_json()


async def test_credential_shaped_draft_is_rejected_before_store_write(store, caplog):
    # Deliberately synthetic credential shape, never a usable key.
    credential = "sk-proj-" + "A1" * 20
    with pytest.raises(StationError, match="credential_input_use_api_key_settings") as exc:
        await store.submit("operator", request(text="Use this key " + credential))
    assert exc.value.status_code == 422
    assert credential not in str(exc.value)
    assert credential not in caplog.text
    snapshot = await store.snapshot()
    assert snapshot.seq == 0 and snapshot.commands == ()


@pytest.mark.parametrize(
    "change",
    [
        {"world_id": "mars:swarm:other"},
        {"station_id": "not-a-station"},
        {"capability_id": "send-email"},
        {"schema_version": 2},
        {"layout_version": True},
        {"agent_id": "forged"},
        {"transform": [0, 0, 0]},
        {"draft": " "},
        {"draft": "x" * 4001},
        {"draft": "null\x00text"},
        {"request_id": "../escape"},
    ],
)
def test_command_rejects_unsupported_scope_versions_and_client_identity(change):
    with pytest.raises(ValidationError):
        StationCommand.model_validate({"request_id": "request", "draft": "A draft", **change})
