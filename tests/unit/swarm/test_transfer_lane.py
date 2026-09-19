"""Slow backup/download I/O cannot consume user-control execution capacity."""

import asyncio
import threading

import pytest

from jarvis.swarm.concurrency import SwarmExecutors
from jarvis.swarm.distributed.database import _CONTROL_LANE, control_lane


@pytest.mark.asyncio
async def test_saturated_transfer_workers_leave_controls_responsive():
    executor = SwarmExecutors()
    entered = threading.Barrier(5)
    release = threading.Event()

    def block():
        entered.wait(timeout=5)
        assert release.wait(5)

    jobs = [asyncio.create_task(executor.transfer(block)) for _ in range(4)]
    try:
        await asyncio.to_thread(entered.wait, 5)
        assert (
            await asyncio.wait_for(executor.call(lambda: "control ready"), timeout=1)
            == "control ready"
        )
    finally:
        release.set()
        await asyncio.gather(*jobs)
        await executor.close()


@pytest.mark.asyncio
async def test_transfers_do_not_borrow_reserved_postgres_control_connections():
    executor = SwarmExecutors()
    try:
        with control_lane():
            assert await executor.transfer(_CONTROL_LANE.get) is False
            assert _CONTROL_LANE.get() is True
        assert await executor.call(_CONTROL_LANE.get) is True
    finally:
        await executor.close()
