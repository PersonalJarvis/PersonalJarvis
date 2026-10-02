"""Start the engine worker as a child process and exchange protocol frames.

Used by the app's provider adapter and by the bench. The worker reads its
stdin and exits when it closes, so closing this client (or the app dying)
ends the worker — the property that replaces pidfiles and port probes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.voice_engine import protocol as p

# Same flag as jarvis.core.process_utils.NO_WINDOW_CREATIONFLAGS (AP-1).
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
EXITED = "_exited"


def worker_env(*, package_root: Path | None = None, home: Path | None = None,
               extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    })
    if package_root is not None:
        env["PYTHONPATH"] = str(package_root)
    if home is not None:
        env["JARVIS_VOICE_ENGINE_HOME"] = str(home)
    env.update(extra or {})
    return env


class EngineClient:
    def __init__(self, python: str, *, env: dict[str, str] | None = None,
                 stderr_path: Path | None = None) -> None:
        self._python = python
        self._env = env or worker_env()
        self._stderr_path = stderr_path
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.audio_frames: asyncio.Queue[p.AudioFrame] = asyncio.Queue()
        self._stderr_handle: Any = None
        self._write_lock = asyncio.Lock()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    async def start(self, timeout_s: float = 30.0) -> dict[str, Any]:
        if self._stderr_path is not None:
            self._stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_handle = self._stderr_path.open("ab")
        self._process = await asyncio.create_subprocess_exec(
            self._python, "-m", "jarvis.voice_engine.worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr_handle or asyncio.subprocess.DEVNULL,
            env=self._env, creationflags=_NO_WINDOW,
        )
        self._reader_task = asyncio.get_running_loop().create_task(self._read())
        return await self.wait_for(lambda m: m["type"] == p.HELLO, timeout_s)

    async def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        reader = p.FrameReader()
        try:
            while True:
                data = await self._process.stdout.read(65536)
                if not data:
                    break
                for frame in reader.feed(data):
                    if isinstance(frame, p.AudioFrame):
                        self.audio_frames.put_nowait(frame)
                    else:
                        self.messages.put_nowait(frame)
        finally:
            self.messages.put_nowait({"type": EXITED})

    async def send(self, message: dict[str, Any]) -> None:
        await self._write(p.encode_json(message))

    async def send_audio(self, slot: int, seq: int, pcm16: bytes) -> None:
        await self._write(p.encode_audio(slot, seq, pcm16))

    async def _write(self, data: bytes) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("the voice engine is not running")
        async with self._write_lock:
            self._process.stdin.write(data)
            await self._process.stdin.drain()

    async def wait_for(self, predicate: Callable[[dict[str, Any]], bool],
                       timeout_s: float) -> dict[str, Any]:
        """Next message matching ``predicate``; other messages are dropped."""
        async with asyncio.timeout(timeout_s):
            while True:
                message = await self.messages.get()
                if message["type"] == EXITED:
                    raise RuntimeError("the voice engine exited")
                if predicate(message):
                    return message

    async def close(self, timeout_s: float = 5.0) -> int | None:
        process = self._process
        if process is None:
            return None
        with contextlib.suppress(RuntimeError, ConnectionError, OSError):
            await self.send({"type": p.SHUTDOWN})
        if process.stdin is not None:
            with contextlib.suppress(ConnectionError, OSError):
                process.stdin.close()
        try:
            code = await asyncio.wait_for(process.wait(), timeout_s)
        except TimeoutError:  # a worker that ignores shutdown is killed instead
            process.kill()
            code = await process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._stderr_handle is not None:
            self._stderr_handle.close()
        self._process = None
        return code
