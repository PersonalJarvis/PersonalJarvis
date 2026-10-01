"""Bounded Swarm execution lanes, isolated from the application's default pool."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from functools import partial
from typing import Any

worker_lane: ContextVar[bool] = ContextVar("swarm_worker_lane", default=False)


class SwarmExecutors:
    def __init__(self) -> None:
        self.work = ThreadPoolExecutor(max_workers=16, thread_name_prefix="swarm-work")
        self.control = ThreadPoolExecutor(max_workers=4, thread_name_prefix="swarm-control")
        self.compute = ThreadPoolExecutor(max_workers=4, thread_name_prefix="swarm-sandbox")
        self.io = ThreadPoolExecutor(max_workers=4, thread_name_prefix="swarm-transfer")
        self._closed = False

    async def call(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        context = copy_context()
        work = worker_lane.get()
        pool = self.work if work else self.control

        def invoke() -> Any:
            if work:
                return function(*args, **kwargs)
            from .distributed.database import control_lane

            with control_lane():
                return function(*args, **kwargs)

        return await asyncio.get_running_loop().run_in_executor(pool, context.run, invoke)

    async def sandbox(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(
            self.compute,
            copy_context().run,
            partial(function, *args, **kwargs),
        )

    async def transfer(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        """Keep large disk/network operations away from dispatch and control lanes."""
        context = copy_context()

        def invoke() -> Any:
            from .distributed.database import control_lane

            with control_lane(False):
                return function(*args, **kwargs)

        return await asyncio.get_running_loop().run_in_executor(self.io, context.run, invoke)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for pool in (self.work, self.control, self.compute, self.io):
            await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)
