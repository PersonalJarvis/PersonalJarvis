"""Cancellation-safe transaction cleanup for the two process-owned Mars journals."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite


async def _finish_rollback(connection: aiosqlite.Connection) -> None:
    # aiosqlite queues work on a thread: canceling its await does not cancel the
    # SQL statement. Rollback is queued behind BEGIN or any pending write and
    # must finish before the caller releases the connection's ownership lock.
    rollback = asyncio.create_task(connection.rollback(), name="mars-sqlite-rollback")
    canceled = False
    while not rollback.done():
        try:
            await asyncio.shield(rollback)
        except asyncio.CancelledError:
            # A second stop/cancel may arrive during cleanup. Keep joining the
            # owned rollback, then propagate cancellation after SQL is settled.
            canceled = True
    rollback.result()
    if canceled:
        raise asyncio.CancelledError


@asynccontextmanager
async def atomic_transaction(
    connection: aiosqlite.Connection,
) -> AsyncIterator[aiosqlite.Connection]:
    """Caller holds its journal lock throughout BEGIN, work and rollback/commit."""
    try:
        await connection.execute("BEGIN IMMEDIATE")
        yield connection
        await connection.commit()
    except BaseException:
        await _finish_rollback(connection)
        raise
