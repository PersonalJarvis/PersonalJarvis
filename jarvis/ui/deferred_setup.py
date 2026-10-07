"""Single-owner off-loop setup that cannot race resource teardown."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class DeferredSetup:
    """Retain an uncancelled setup task until its owner can close its resources.

    Cancelling a caller of ``asyncio.to_thread`` does not stop its thread.
    Shield the owned task and join it before teardown instead of allowing a
    cancelled waiter to leave construction running behind resource cleanup.
    Calls and shutdown run on the owning event loop; only ``build`` runs off it.
    """

    def __init__(self, build: Callable[[], None], *, ready: bool = False) -> None:
        self._build = build
        self._ready = ready
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    async def prepare(self) -> None:
        if self._stopping:
            raise RuntimeError("Application setup is stopping")
        if not self._ready:
            if self._task is None:
                self._task = asyncio.create_task(
                    asyncio.to_thread(self._build), name="app-feature-setup"
                )
            await asyncio.shield(self._task)
            self._ready = True
        if self._stopping:
            raise RuntimeError("Application setup stopped before publication")

    async def stop(self) -> None:
        """Fence new preparation and join construction before resource cleanup.

        A build failure is re-raised to the owner, which must log it and still
        close any partially constructed resources. A failed setup is never
        retried against an app containing a partial route graph.
        """
        self._stopping = True
        if self._task is not None:
            await asyncio.shield(self._task)
