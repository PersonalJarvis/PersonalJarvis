"""Cache heartbeat: send a cheap request every ~240 s to keep the
Anthropic prompt cache warm.

The cache has a ~5-minute TTL. If the system is idle for longer than
5 minutes the cache expires and the next brain call pays ~10x more.
A cheap heartbeat prevents that.

The heartbeat is opt-in (via `BrainPolicyConfig.prompt_cache_heartbeat_seconds`)
and is only started for Anthropic providers.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class CacheHeartbeat:
    """Periodic callable runner. Can be started as an asyncio.Task."""

    def __init__(
        self,
        interval_s: float,
        probe: Callable[[], Awaitable[Any]],
        *,
        name: str = "cache-heartbeat",
    ) -> None:
        self._interval_s = interval_s
        self._probe = probe
        self._name = name
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)
                    break  # stop signal received
                except TimeoutError:
                    pass  # timer elapsed — send probe
                try:
                    await self._probe()
                except Exception:  # noqa: BLE001
                    # Heartbeat failure is non-fatal; next attempt in interval_s.
                    from loguru import logger
                    logger.debug(f"[{self._name}] probe failed, continuing")
        except asyncio.CancelledError:
            pass

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name=self._name)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
