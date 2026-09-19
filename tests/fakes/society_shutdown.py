"""Event-controlled Society owners for admission and shutdown tests."""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.society.shutdown import AdmissionGate


class PausedOwner:
    """Expose cancellation and completion separately, without timed fake work."""

    def __init__(self, *, resist_cancel: bool = False, hold_cleanup: bool = False) -> None:
        self.resist_cancel = resist_cancel
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cancelled_again = asyncio.Event()
        self.release = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancel_requests = 0
        self.admitted: bool | None = None
        if not hold_cleanup:
            self.cleanup_release.set()

    async def run(self) -> None:
        self.entered.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_requests += 1
                    self.cancelled.set()
                    if self.cancel_requests > 1:
                        self.cancelled_again.set()
                    if not self.resist_cancel:
                        await self.cleanup_release.wait()
                        raise
                    # Deliberately emulate an owner that refuses cancellation.
                    # Tests must release it explicitly, even after a failed assertion.
        finally:
            self.finished.set()

    async def run_admitted(self, gate: AdmissionGate) -> None:
        async with gate.admit() as admitted:
            self.admitted = admitted
            if admitted:
                await self.run()

    async def cleanup(self, *tasks: asyncio.Task[Any]) -> None:
        """Release every fake wait and retrieve all task outcomes on any test exit."""
        self.release.set()
        self.cleanup_release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1)
