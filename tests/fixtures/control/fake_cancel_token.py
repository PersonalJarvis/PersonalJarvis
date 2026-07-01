"""Fake CancelToken for tests — structurally compatible with the protocol.

The real implementation in `jarvis.control.cancel` lands with Task 4.
"""
from __future__ import annotations

import asyncio


class FakeCancelToken:
    """Minimal protocol-compatible implementation with extra test hooks."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None
        self.cancel_calls: list[str] = []

    def cancel(self, reason: str) -> None:
        self.cancel_calls.append(reason)
        if self._reason is None:
            self._reason = reason
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait_until_cancelled(self) -> None:
        await self._event.wait()
