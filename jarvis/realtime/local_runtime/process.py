"""Application-owned native inference over pipes with one cross-process lease.

No model code is imported in Jarvis. Native crashes and hangs are reclaimed by
reaping the owned child, and recovery always creates a new inference instance.
The command is constructed by a trusted adapter, never read from model metadata.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .events import NativeAudioError, NativeAudioEvent

log = logging.getLogger(__name__)
_MAX_EVENT_BYTES = 4 * 1024 * 1024
_MAX_REQUEST_BYTES = 32 * 1024 * 1024
_AUDIO_RATES = frozenset({16_000, 24_000, 44_100, 48_000})


class NativeAudioProcess:
    """A warm worker, separate from a leased conversation and from wake readiness."""

    def __init__(
        self,
        command: tuple[str, ...],
        runtime_directory: Path,
        *,
        expected_revision: str,
        start_timeout_s: float = 120.0,
        event_timeout_s: float = 60.0,
        cancel_timeout_s: float = 3.0,
    ) -> None:
        self._command = command
        self._directory = runtime_directory
        self._revision = expected_revision
        self._start_timeout = start_timeout_s
        self._event_timeout = event_timeout_s
        self._cancel_timeout = cancel_timeout_s
        self._process: subprocess.Popen[bytes] | None = None
        self._tree: Any = None
        self._lease: Any = None
        self._reader: threading.Thread | None = None
        self._reader_done = threading.Event()
        self._reader_stop = threading.Event()
        self._reader_failure = ""
        self._events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        self._read_task: asyncio.Task[dict[str, Any]] | None = None
        self._write_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._retired_cancellations: set[str] = set()
        self._active_id = ""
        self._cancelled_id = ""
        self._context_valid = False
        self._loaded = False
        self._closed = False
        self.loaded_capabilities: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def usable(self) -> bool:
        return (
            self._loaded
            and not self._closed
            and not self._reader_done.is_set()
            and not self._reader_failure
            and self.running
        )

    @property
    def busy(self) -> bool:
        return self._turn_lock.locked()

    def discard_context(self) -> None:
        """Start a new conversation without exposing the previous caller's state."""
        if self.busy:
            raise NativeAudioError(
                "Wait for the native turn to finish before changing conversations."
            )
        self._context_valid = False

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    def _spawn(self) -> None:
        with self._process_lock:
            self._spawn_locked()

    def _spawn_locked(self) -> None:
        if self._closed:
            raise NativeAudioError("Native runtime startup was cancelled.")
        from filelock import FileLock, Timeout

        from jarvis.core.process_tree import make_process_tree

        from .packages import _linked

        if not self._command or not Path(self._command[0]).is_absolute():
            raise NativeAudioError("The native runtime executable must be installed first.")
        self._directory.mkdir(parents=True, exist_ok=True)
        lock_path = self._directory / ".native-voice.lock"
        if lock_path.exists() or lock_path.is_symlink():
            metadata = lock_path.lstat()
            if _linked(metadata) or metadata.st_nlink != 1:
                raise NativeAudioError("The native runtime ownership lock is unsafe.")
        lease = FileLock(lock_path, timeout=0, thread_local=False)
        try:
            lease.acquire()
        except Timeout as exc:
            raise NativeAudioError(
                "Another Jarvis process already owns the native voice runtime."
            ) from exc
        self._lease = lease
        try:
            self._tree = make_process_tree("local-native-voice")
            if not self._tree.supports_containment:
                raise NativeAudioError("Native process containment is unavailable on this host.")
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                bufsize=0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
                start_new_session=os.name != "nt",
            )
            self._tree.assign(self._process.pid)
            self._reader = threading.Thread(
                target=self._read_stdout, name="native-audio-reader", daemon=True
            )
            self._reader.start()
        except BaseException:
            self._stop_blocking()
            raise

    def _read_stdout(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while line := process.stdout.readline(_MAX_EVENT_BYTES + 1):
                if len(line) > _MAX_EVENT_BYTES or not line.endswith(b"\n"):
                    raise ValueError("unbounded native event")
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("native event is not an object")
                deadline = time.monotonic() + 5
                while not self._reader_stop.is_set():
                    try:
                        self._events.put(event, timeout=0.1)
                        break
                    except queue.Full:
                        if time.monotonic() >= deadline:
                            raise
                if self._reader_stop.is_set():
                    return
        except (OSError, ValueError, queue.Full):
            self._reader_failure = (
                "The native worker returned an invalid or blocked control stream."
            )
            log.warning("Native audio control stream failed; discarding its context")
            # Only this Popen handle is ours. Never locate/kill by process name.
            if process.poll() is None:
                process.terminate()
        finally:
            self._reader_done.set()

    def _take_event(self) -> dict[str, Any]:
        while True:
            try:
                return self._events.get(timeout=0.1)
            except queue.Empty:
                if self._reader_done.is_set():
                    raise NativeAudioError(
                        self._reader_failure or "The native worker exited."
                    ) from None

    async def _next_event(self, budget_s: float) -> dict[str, Any]:
        if self._read_task is None:
            self._read_task = asyncio.create_task(asyncio.to_thread(self._take_event))
        task = self._read_task
        try:
            # A cancelled await must not abandon a queue getter that could eat
            # the cancellation receipt. Cleanup resumes the SAME pending read.
            return await asyncio.wait_for(asyncio.shield(task), timeout=budget_s)
        except TimeoutError as exc:
            raise NativeAudioError("The native worker stopped responding.") from exc
        finally:
            if task.done():
                self._read_task = None

    async def start(self) -> None:
        if self._closed:
            raise NativeAudioError("This native runtime was closed; create a fresh instance.")
        if self.usable:
            return
        if not self._lifecycle_lock.acquire(blocking=False):
            raise NativeAudioError("The native runtime is already starting or stopping.")
        spawn: asyncio.Task[None] | None = None
        try:
            if self._process is not None:
                raise NativeAudioError("This native runtime failed; create a fresh instance.")
            spawn = asyncio.create_task(asyncio.to_thread(self._spawn))
            await asyncio.shield(spawn)
            event = await self._next_event(self._start_timeout)
            if (
                event.get("kind") != "loaded"
                or type(event.get("protocol")) is not int
                or event["protocol"] != 1
                or event.get("revision") != self._revision
            ):
                raise NativeAudioError("The installed native runtime has an incompatible protocol.")
            self.loaded_capabilities = event
            if self._closed:
                raise NativeAudioError("Native runtime startup was cancelled.")
            self._loaded = True
        except BaseException:
            if spawn is not None:
                await asyncio.shield(asyncio.gather(spawn, return_exceptions=True))
            await asyncio.shield(asyncio.to_thread(self._stop_blocking))
            if self._read_task is not None:
                await asyncio.gather(self._read_task, return_exceptions=True)
                self._read_task = None
            self._closed = True
            raise
        finally:
            self._lifecycle_lock.release()

    def _write(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
        if len(data) > _MAX_REQUEST_BYTES:
            raise NativeAudioError("This audio turn exceeds the native runtime's input capacity.")
        with self._write_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise NativeAudioError("The native voice runtime is not running.")
            try:
                view = memoryview(data)
                while view:
                    written = process.stdin.write(view)
                    if not written:
                        raise OSError("native input closed")
                    view = view[written:]
                process.stdin.flush()
            except OSError as exc:
                raise NativeAudioError("The native worker input pipe closed.") from exc

    async def _send(self, payload: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(self._write, payload), timeout=5)
        except TimeoutError as exc:
            await self.close()
            raise NativeAudioError("The native worker is not accepting input.") from exc

    async def interrupt(self) -> None:
        request_id = self._active_id
        if request_id and self.running and self._cancelled_id != request_id:
            self._cancelled_id = request_id
            self._context_valid = False
            await self._send({"id": request_id, "command": "cancel"})

    async def generate(
        self,
        *,
        text: str = "",
        audio_wav: bytes | None = None,
        instructions: str = "Respond with interleaved text and audio.",
        continue_context: bool = False,
        output_mode: Literal["text", "audio", "text_audio"] = "text_audio",
        max_tokens: int = 32768,
    ) -> AsyncIterator[NativeAudioEvent]:
        if not self.usable:
            raise NativeAudioError("The native audio model is not loaded.")
        if not self._turn_lock.acquire(blocking=False):
            raise NativeAudioError("The native audio model is already handling a turn.")
        request_id = uuid.uuid4().hex
        terminal = False
        sent = False
        try:
            if continue_context and not self._context_valid:
                raise NativeAudioError("The native conversation must be reset before continuing.")
            if not text.strip() and audio_wav is None:
                raise ValueError("Audio or text input is required.")
            if output_mode not in {"text", "audio", "text_audio"}:
                raise ValueError("Unknown native output mode.")
            if type(max_tokens) is not int or not 1 <= max_tokens <= 32768:
                raise ValueError("max_tokens must fit the native model context.")
            self._active_id = request_id
            self._context_valid = False
            payload: dict[str, Any] = {
                "id": request_id,
                "command": "generate",
                "text": text,
                "instructions": instructions,
                "reset_context": not continue_context,
                "output_mode": output_mode,
                "max_tokens": max_tokens,
            }
            if audio_wav is not None:
                payload["audio_wav"] = base64.b64encode(audio_wav).decode("ascii")
            # A failed write is uncertain: cleanup reaps the worker instead of
            # assuming it did not receive part/all of this request.
            sent = True
            await self._send(payload)
            while True:
                event = await self._next_event(self._event_timeout)
                if self._closed:
                    raise NativeAudioError("The native voice conversation was closed.")
                if not isinstance(event.get("id"), str):
                    raise NativeAudioError(
                        "The native worker returned an invalid generation identity."
                    )
                if (
                    event.get("id") in self._retired_cancellations
                    and event.get("kind") == "error"
                    and event.get("code") == "not_active"
                ):
                    self._retired_cancellations.discard(event["id"])
                    continue
                if event.get("id") != request_id:
                    raise NativeAudioError("The native worker mixed conversation generations.")
                kind = event.get("kind")
                if kind in {"done", "cancelled", "error"}:
                    terminal = True
                    if kind == "error":
                        raise NativeAudioError("The native model could not complete this turn.")
                    self._context_valid = kind == "done" and self._cancelled_id != request_id
                    yield NativeAudioEvent(kind="done" if self._context_valid else "cancelled")
                    break
                if kind == "text":
                    value = event.get("text")
                    if not isinstance(value, str):
                        raise NativeAudioError("The native worker returned an invalid transcript.")
                    if output_mode != "audio" and self._cancelled_id != request_id:
                        yield NativeAudioEvent(kind="text", text=value)
                elif kind == "audio":
                    rate = event.get("sample_rate")
                    if type(rate) is not int or rate not in _AUDIO_RATES:
                        raise NativeAudioError(
                            "The native worker returned an unsupported sample rate."
                        )
                    try:
                        pcm = base64.b64decode(event.get("pcm", ""), validate=True)
                    except (TypeError, ValueError) as exc:
                        raise NativeAudioError("The native worker returned invalid PCM.") from exc
                    if not pcm or len(pcm) % 2:
                        raise NativeAudioError("The native worker returned incomplete PCM samples.")
                    if output_mode != "text" and self._cancelled_id != request_id:
                        yield NativeAudioEvent(kind="audio", pcm=pcm, sample_rate=rate)
                else:
                    raise NativeAudioError("The native worker returned an unknown event.")
        except NativeAudioError:
            if sent:
                # A broken wire or native inference error cannot be repaired by
                # resetting a counter on this instance (AP-24). Preserve the
                # application conversation, but require a fresh native process.
                await self.close()
            raise
        finally:
            try:
                if sent and not terminal and not self._closed:
                    self._context_valid = False
                    try:
                        await self.interrupt()
                        deadline = time.monotonic() + self._cancel_timeout
                        while time.monotonic() < deadline:
                            event = await self._next_event(max(0.01, deadline - time.monotonic()))
                            if event.get("id") == request_id and event.get("kind") in {
                                "done",
                                "cancelled",
                                "error",
                            }:
                                terminal = True
                                break
                    except (NativeAudioError, OSError):
                        log.info("Reclaiming an unresponsive native audio worker")
                    finally:
                        if not terminal:
                            await self.close()
            finally:
                if self._cancelled_id == request_id:
                    # A cancellation can cross a terminal event in the pipe.
                    # Only the explicit "not_active" reply for this retired ID
                    # may be ignored later; stale audio/text remains an error.
                    if len(self._retired_cancellations) >= 32:
                        self._retired_cancellations.clear()
                    self._retired_cancellations.add(request_id)
                self._active_id = ""
                self._turn_lock.release()

    def _stop_blocking(self) -> None:
        with self._process_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._reader_stop.set()
        process = self._process
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    log.info("Native worker ignored termination; killing the owned process")
                    process.kill()
                    process.wait(timeout=3)
        finally:
            if self._tree is not None:
                self._tree.close()
                self._tree = None
            if self._reader is not None:
                self._reader.join(timeout=3)
            self._reader_done.set()
            if process is not None:
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
            if self._lease is not None:
                self._lease.release()
                self._lease = None
            self._loaded = False

    async def close(self) -> None:
        first_close = not self._closed
        self._closed = True
        self._context_valid = False
        shutdown: asyncio.Task[None] | None = None
        try:
            if first_close and self.running:
                shutdown = asyncio.create_task(
                    asyncio.to_thread(
                        self._write,
                        {"id": uuid.uuid4().hex, "command": "shutdown"},
                    )
                )
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.25)
                    process = self._process
                    if process is not None:
                        await asyncio.to_thread(process.wait, timeout=0.5)
                except (TimeoutError, NativeAudioError, subprocess.TimeoutExpired):
                    log.debug("Native worker needs forced shutdown")
        finally:
            await asyncio.shield(asyncio.to_thread(self._stop_blocking))
            if shutdown is not None:
                await asyncio.gather(shutdown, return_exceptions=True)
            if self._read_task is not None:
                task = self._read_task
                if task.get_loop() is asyncio.get_running_loop():
                    await asyncio.gather(task, return_exceptions=True)
                # On another loop the reader's EOF wakes its owner. Never await
                # a Task bound to that foreign loop during desktop shutdown.
                self._read_task = None
