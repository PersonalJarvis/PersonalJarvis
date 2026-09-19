"""Transient admission fencing and bounded joins for Society lifecycle owners."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)
QUIESCE_TIMEOUT_S = 5.0


async def cancel_and_join(tasks: Iterable[asyncio.Task[Any]], label: str) -> None:
    active = {task for task in tasks if not task.done()}
    if not active:
        return
    if asyncio.current_task() in active:
        raise RuntimeError(f"Cannot join the current {label} owner during shutdown")
    for task in active:
        task.cancel()
    done, pending = await asyncio.wait(active, timeout=QUIESCE_TIMEOUT_S)
    for task in done:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                log.warning("%s cleanup ended with %s", label, type(error).__name__)
    if pending:
        log.warning("%s still has %s owners after cancellation", label, len(pending))
        raise RuntimeError(f"{label} did not stop; its storage must remain open")


class AdmissionGate:
    """Receipt completion means the hook ended, never merely that a waiter canceled."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.accepting = True
        self._active: dict[asyncio.Future[None], asyncio.Task[Any]] = {}

    @asynccontextmanager
    async def admit(self) -> AsyncIterator[bool]:
        if not self.accepting:
            yield False
            return
        owner = asyncio.current_task()
        assert owner is not None
        finished: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._active[finished] = owner
        try:
            yield True
        finally:
            self._active.pop(finished, None)
            if not finished.done():
                finished.set_result(None)

    async def quiesce(self) -> None:
        self.accepting = False
        active = list(self._active)
        if not active:
            return
        # asyncio.wait never cancels receipt futures when this waiter is
        # canceled. A later retry must still observe the actual hook owner.
        _, pending = await asyncio.wait(active, timeout=QUIESCE_TIMEOUT_S)
        if not pending:
            return
        owners = {self._active[receipt] for receipt in pending if receipt in self._active}
        if asyncio.current_task() in owners:
            raise RuntimeError(f"Cannot join the current {self.label} admission")
        log.warning("%s canceling %s stalled admissions", self.label, len(owners))
        for owner in owners:
            owner.cancel()
        _, remaining = await asyncio.wait(pending, timeout=QUIESCE_TIMEOUT_S)
        if remaining:
            log.warning("%s admissions did not stop after cancellation", self.label)
            raise RuntimeError(f"{self.label} did not stop; its storage must remain open")
