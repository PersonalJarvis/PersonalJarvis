"""Prepare one pixel-free recording process and transfer it on explicit start."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)
_READY_TIMEOUT_S = 20.0


class RecordingRuntime:
    """Own a dormant worker until RecordingService takes responsibility for it."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._active = False

    async def prewarm(self) -> None:
        async with self._lock:
            if not self._active:
                await self._ensure()

    async def _ensure(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        await self._dispose()
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-m", "jarvis.appshot.recording_worker", "--standby",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, creationflags=NO_WINDOW_CREATIONFLAGS,
        ))
        try:
            try:
                self._process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                self._process = await spawn  # A cancelled spawn still owns its late child.
                raise
            async with asyncio.timeout(_READY_TIMEOUT_S):
                assert self._process.stdout is not None
                while raw := await self._process.stdout.readline():
                    try:
                        event = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeError):
                        log.debug("appshot: ignoring non-protocol recorder startup output")
                        continue
                    if isinstance(event, dict) and event.get("phase") == "ready":
                        return self._process
                raise OSError("The recording runtime closed before becoming ready.")
        except BaseException:
            await self._dispose()
            raise

    async def take(self, output: Path, language: str) -> asyncio.subprocess.Process:
        async with self._lock:
            process = await self._ensure()
            try:
                assert process.stdin is not None
                command = {"cmd": "start", "output": str(output), "language": language}
                process.stdin.write((json.dumps(command) + "\n").encode("utf-8"))
                await process.stdin.drain()
            except BaseException:
                await self._dispose()
                raise
            self._process = None  # The recording service now owns shutdown and stdout.
            self._active = True
            return process

    def release(self) -> None:
        """The transferred worker has exited; another standby may now be prepared."""
        self._active = False

    async def _dispose(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()  # EOF quits standby without ever starting capture.
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            log.debug("appshot: terminating an unresponsive standby recorder")
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def close(self) -> None:
        async with self._lock:
            await self._dispose()
