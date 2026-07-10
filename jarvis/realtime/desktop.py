"""Desktop playback adapter for the transport-neutral realtime session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.protocols import AudioChunk


class DesktopRealtimePlayback:
    """Feed provider PCM deltas through one persistent ``AudioPlayer`` stream.

    The bounded queue applies backpressure when an output device is slower than
    the provider. A turn-complete marker drains naturally; barge-in or teardown
    stops the device immediately and discards queued audio.
    """

    def __init__(
        self,
        player: Any,
        *,
        sample_rate: int = 24_000,
        max_queue_chunks: int = 200,
        finish_timeout_s: float = 120.0,
    ) -> None:
        self._player = player
        self._sample_rate = int(sample_rate)
        self._max_queue_chunks = max(1, int(max_queue_chunks))
        self._finish_timeout_s = max(1.0, float(finish_timeout_s))
        self._queue: asyncio.Queue[AudioChunk | None] | None = None
        self._task: asyncio.Task[None] | None = None

    async def send_binary(self, pcm: bytes) -> None:
        if not pcm:
            return
        if self._task is None or self._task.done():
            self._queue = asyncio.Queue(maxsize=self._max_queue_chunks)
            self._task = asyncio.create_task(
                self._player.play_chunks(self._chunks(self._queue)),
                name="realtime-desktop-playback",
            )
        assert self._queue is not None
        await self._queue.put(
            AudioChunk(pcm=bytes(pcm), sample_rate=self._sample_rate, timestamp_ns=0)
        )

    def set_sample_rate(self, sample_rate: int) -> None:
        """Set the rate announced by the accepted provider handshake.

        Providers must announce this before their first audio delta. Refuse a
        mid-stream rate change because one ``AudioPlayer`` stream cannot safely
        reinterpret already queued PCM at a different rate.
        """
        rate = int(sample_rate)
        if rate <= 0:
            raise ValueError("Realtime output sample rate must be positive")
        if self._task is not None and not self._task.done():
            raise RuntimeError("Cannot change realtime sample rate during playback")
        self._sample_rate = rate

    async def finish_turn(self) -> None:
        queue, task = self._detach()
        if queue is None or task is None:
            return
        await queue.put(None)
        try:
            await asyncio.wait_for(task, timeout=self._finish_timeout_s)
        except TimeoutError:
            self._player.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def cancel(self) -> None:
        queue, task = self._detach()
        self._player.stop()
        if queue is None or task is None:
            return
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - race-safe guard
                break
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:  # pragma: no cover - queue was drained above
            pass
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        await self.cancel()

    def _detach(
        self,
    ) -> tuple[asyncio.Queue[AudioChunk | None] | None, asyncio.Task[None] | None]:
        queue, task = self._queue, self._task
        self._queue = None
        self._task = None
        return queue, task

    @staticmethod
    async def _chunks(
        queue: asyncio.Queue[AudioChunk | None],
    ) -> AsyncIterator[AudioChunk]:
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            yield chunk


__all__ = ["DesktopRealtimePlayback"]
