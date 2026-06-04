"""Base classes for harness plugins.

A harness starts a sub-agent process (OpenClaw, Codex, OI, Hermes,
Python script) and streams progress back. All harnesses are plugin classes
behind the `Harness` protocol. The `SubprocessHarness` base class handles
the common parts (spawning the subprocess, reading UTF-8 line by line,
yielding events, cancellation).

Each concrete harness class overrides three methods:
  - `build_command(task)` — CLI arguments
  - `environment(task)` — env variables (merged with task.env)
  - `parse_chunk(line)` — NDJSON/text line to HarnessResult
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import AsyncIterator

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import HarnessResult, HarnessTask

log = logging.getLogger(__name__)


class SubprocessHarness:
    """Common base for all harnesses that run as a subprocess.

    Subclasses must implement at least `build_command`.
    `parse_chunk` has a sensible default (yield stdout line as text).
    """

    name: str = "subprocess"
    version: str = "0.1.0"
    supports_versions: str = ">=0.1"

    def __init__(self) -> None:
        # Last active subprocess — used by ``cancel()`` as a best-effort hook.
        # WARNING: overwritten by concurrent ``invoke()`` calls;
        # invocation-specific variables live in the invoke() body.
        self._process: asyncio.subprocess.Process | None = None
        self._cancelled = False
        # Per-invocation process set for concurrent cancel: each invoke()
        # registers its process and removes it when done. cancel() kills ALL
        # registered processes. Bug 2026-04-29: previously, 3 parallel
        # multi_spawn invoke() calls on the singleton instance could race
        # because ``self._process`` was overwritten — on the first subprocess
        # crash the exception propagated uncaught through the other invocations.
        self._active_processes: set[asyncio.subprocess.Process] = set()

    # ------------------------------------------------------------------
    # To override
    # ------------------------------------------------------------------

    def build_command(self, task: HarnessTask) -> list[str]:
        raise NotImplementedError

    def environment(self, task: HarnessTask) -> dict[str, str]:
        """Default: current ENV + task ENV, UTF-8 forced for Python subprocesses."""
        env = dict(os.environ)
        env.update(task.env or {})
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        return env

    def parse_chunk(self, line: str, *, is_stderr: bool = False) -> HarnessResult | None:
        """Default parser: wraps each line as stdout/stderr progress."""
        line = (line or "").rstrip("\r\n")
        if not line:
            return None
        if is_stderr:
            return HarnessResult(stderr=line + "\n", is_final=False)
        return HarnessResult(stdout=line + "\n", is_final=False)

    # ------------------------------------------------------------------
    # Protocol-Implementation
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Default: try ``build_command(task)`` with a dummy task and
        check whether the binary exists (subprocess.which equivalent)."""
        try:
            cmd = self.build_command(HarnessTask(prompt="__health__"))
        except Exception:  # noqa: BLE001
            return False
        if not cmd:
            return False
        import shutil
        return shutil.which(cmd[0]) is not None

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        """Starts the subprocess, yields progress results, final result at the end.

        **Concurrency-safe**: invocation-local variables (``proc``,
        ``stdout_task``, ``stderr_task``, ``queue``) — no self-state writes
        that could race between parallel ``invoke()`` calls. Cancel tracking
        via ``self._active_processes`` (set, safe for concurrent add/discard).
        """
        t_start = time.perf_counter()
        cmd = self.build_command(task)
        env = self.environment(task)
        cwd = task.cwd or None

        log.info("[%s] start cmd=%s", self.name, " ".join(cmd[:6]) + ("..." if len(cmd) > 6 else ""))

        proc: asyncio.subprocess.Process
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except FileNotFoundError as exc:
            yield HarnessResult(
                stderr=f"Harness-Binary nicht gefunden: {exc}\n",
                exit_code=127,
                duration_ms=int((time.perf_counter() - t_start) * 1000),
                is_final=True,
            )
            return
        except OSError as exc:
            yield HarnessResult(
                stderr=f"Spawn-Fehler: {exc}\n",
                exit_code=1,
                duration_ms=int((time.perf_counter() - t_start) * 1000),
                is_final=True,
            )
            return
        except NotImplementedError as exc:
            # Windows-specific: SelectorEventLoop does not support subprocess
            # — some setups (old aiohttp, Cygwin hybrid) force this. Instead of
            # crashing through the stack we return a clear final HarnessResult.
            yield HarnessResult(
                stderr=(
                    f"Subprocess nicht unterstuetzt im aktuellen Event-Loop: {exc}. "
                    "Pruefe ob WindowsProactorEventLoopPolicy aktiv ist.\n"
                ),
                exit_code=1,
                duration_ms=int((time.perf_counter() - t_start) * 1000),
                is_final=True,
            )
            return

        # Register active process for cancel() tracking.
        self._active_processes.add(proc)
        self._process = proc  # best-effort for cancel() (not race-free)

        async def _read_stream(stream: asyncio.StreamReader, *, is_stderr: bool) -> AsyncIterator[HarnessResult]:
            while True:
                try:
                    raw = await asyncio.wait_for(stream.readline(), timeout=task.timeout_s)
                except TimeoutError:
                    yield HarnessResult(
                        stderr=f"Timeout nach {task.timeout_s}s — kill subprocess\n",
                        exit_code=-1,
                        is_final=False,
                    )
                    if proc.returncode is None:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                    return
                if not raw:
                    return
                try:
                    line = raw.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
                chunk = self.parse_chunk(line, is_stderr=is_stderr)
                if chunk is not None:
                    yield chunk

        # Interleaved read via asyncio.as_completed is fiddly — we use a queue.
        queue: asyncio.Queue[HarnessResult | None] = asyncio.Queue()

        async def _pump(stream: asyncio.StreamReader, *, is_stderr: bool) -> None:
            async for chunk in _read_stream(stream, is_stderr=is_stderr):
                await queue.put(chunk)
            await queue.put(None)  # sentinel for this stream

        stdout_task = asyncio.create_task(_pump(proc.stdout, is_stderr=False))  # type: ignore[arg-type]
        stderr_task = asyncio.create_task(_pump(proc.stderr, is_stderr=True))  # type: ignore[arg-type]

        finished_streams = 0
        try:
            while finished_streams < 2:
                item = await queue.get()
                if item is None:
                    finished_streams += 1
                    continue
                yield item
        finally:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            stdout_task.cancel()
            stderr_task.cancel()
            self._active_processes.discard(proc)

        duration_ms = int((time.perf_counter() - t_start) * 1000)
        yield HarnessResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            duration_ms=duration_ms,
            is_final=True,
        )

    async def cancel(self) -> None:
        """Kills all active subprocesses of this harness instance."""
        self._cancelled = True
        # Snapshot during iteration (otherwise RuntimeError on concurrent discard)
        for proc in list(self._active_processes):
            if proc.returncode is not None:
                continue
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except TimeoutError:
                    proc.kill()
            except ProcessLookupError:
                pass


# ----------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------

def is_windows() -> bool:
    return sys.platform == "win32"


def python_executable() -> str:
    return sys.executable
