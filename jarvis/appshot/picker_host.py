"""A warm, pixel-free picker runtime; one visible selection at a time.

Qt lives in a child process. Only an explicit pick command takes a fresh
desktop image; standby imports the GUI without opening a window. Readers are
joined before reuse and a cancelled or timed-out request discards its process,
so a late result can never complete the next request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import time
from typing import Any

from jarvis.appshot import picker as wire
from jarvis.appshot import region

log = logging.getLogger(__name__)
_START_TIMEOUT_S = 5.0


class PickerHost:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._active: asyncio.Task | None = None

    async def prewarm(self) -> None:
        if not region.picker_capability()[0]:
            return
        async with self._lock:
            await self._ensure()

    @staticmethod
    def _standby(proc: subprocess.Popen[str]) -> bool:
        assert proc.stdout is not None
        for line in proc.stdout:
            payload = wire.decode(line)
            if payload and payload.get("event") == wire.EVENT_STANDBY:
                return True
        return False

    @staticmethod
    def _send(proc: subprocess.Popen[str], payload: dict) -> None:
        if proc.stdin is None or proc.poll() is not None:
            raise region.RegionUnavailable("The selection overlay stopped unexpectedly.")
        proc.stdin.write(wire.encode(payload))
        proc.stdin.flush()

    async def _dispose(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            await asyncio.to_thread(self._send, proc, {"cmd": wire.CMD_QUIT})
        except (OSError, ValueError, region.RegionUnavailable):
            log.debug("appshot: picker pipe already closed during shutdown")
        await asyncio.to_thread(region._reap, proc)

    async def _ensure(self) -> subprocess.Popen[str]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        await self._dispose()
        spawn = asyncio.create_task(asyncio.to_thread(region._spawn, resident=True))
        reader = None
        try:
            try:
                self._proc = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                # to_thread cannot cancel Popen: acquire the late child first.
                self._proc = await spawn
                raise
            reader = asyncio.create_task(asyncio.to_thread(self._standby, self._proc))
            ready = await asyncio.wait_for(asyncio.shield(reader), _START_TIMEOUT_S)
            if not ready:
                code = self._proc.poll()
                raise region.RegionUnavailable(region._exit_message(code or 1))
            return self._proc
        except BaseException:
            await self._dispose()
            if reader is not None:
                await reader  # reap closes stdout, including a stuck startup
            raise

    async def pick(self, timeout_s: float, language: str) -> tuple[Any, int | None, bool]:
        async with self._lock:
            self._active = asyncio.current_task()
            try:
                return await self._pick(timeout_s, language)
            finally:
                self._active = None

    async def _pick(self, timeout_s: float, language: str) -> tuple[Any, int | None, bool]:
        started = time.perf_counter()
        proc = await self._ensure()
        try:
            layout = await asyncio.to_thread(region.snap_layout)
        except Exception:
            log.debug("appshot: snap layout unavailable", exc_info=True)
            layout = {}
        escape = None
        reader = None
        payload = None
        timed_out = False
        try:
            await asyncio.to_thread(
                self._send, proc, {"cmd": wire.CMD_START, "language": language, **layout}
            )
            loop = asyncio.get_running_loop()
            escape = loop.create_task(region._escape_cancels(proc), name="appshot-region-esc")

            def marking() -> None:
                loop.call_soon_threadsafe(escape.cancel)

            def ready() -> None:
                log.info(
                    "appshot: picker visible in %.1f ms", (time.perf_counter() - started) * 1000
                )

            reader = asyncio.create_task(
                asyncio.to_thread(region._read_result, proc, marking, ready)
            )
            try:
                payload = await asyncio.wait_for(asyncio.shield(reader), timeout_s)
            except TimeoutError:
                log.info("appshot: selection timed out; closing the picker")
                timed_out = True
        finally:
            if escape is not None:
                escape.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await escape
            if payload is None:
                await self._dispose()
            if reader is not None:
                await reader
        return payload, proc.poll(), timed_out

    async def close(self) -> None:
        active = self._active
        if active is not None and active is not asyncio.current_task():
            active.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active
        async with self._lock:
            await self._dispose()


_host: PickerHost | None = None


def get_picker_host() -> PickerHost:
    global _host
    if _host is None:
        _host = PickerHost()
    return _host


async def close_picker_host() -> None:
    global _host
    host, _host = _host, None
    if host is not None:
        await host.close()
