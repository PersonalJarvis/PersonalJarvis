"""User-started, local screen recordings in an isolated desktop sidecar.

No GUI, capture framework or encoder is imported by the web server. Closing
the parent pipe stops the sidecar; only finalized files are downloadable.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
ACTIVE_PHASES = frozenset({"selecting", "recording", "stopping"})


def recording_dir() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "appshot-recordings"


def recording_file(recording_id: str) -> Path | None:
    """Resolve only finalized recordings, never arbitrary client paths."""
    if not re.fullmatch(r"[0-9a-f]{32}", recording_id):
        return None
    path = recording_dir() / f"{recording_id}.mp4"
    return path if path.is_file() and not path.is_symlink() else None


def recent_recordings() -> list[dict[str, Any]]:
    """Keep finalized videos discoverable after navigation and app restarts."""
    videos = []
    for path in recording_dir().glob("*.mp4"):
        if recording_file(path.stem) is not None:
            try:
                videos.append({"id": path.stem, "created_at": path.stat().st_mtime})
            except OSError:
                log.debug("appshot: recording disappeared during listing", exc_info=True)
    return sorted(videos, key=lambda video: video["created_at"], reverse=True)[:10]


def capability() -> dict[str, Any]:
    from jarvis.platform.probes import display_present, is_wayland

    detail = ""
    permission = False
    if not display_present():
        detail = "Screen recording requires an interactive desktop."
    elif not all(importlib.util.find_spec(name) for name in ("PySide6", "av", "numpy")):
        detail = "Screen recording requires the desktop capture and video packages."
    elif is_wayland() and importlib.util.find_spec("PySide6.QtMultimedia") is None:
        detail = "Wayland recording requires Qt Multimedia, PipeWire and a ScreenCast portal."
    elif sys.platform == "darwin":
        from jarvis.platform import screen_access

        permission = not screen_access.state_allows_capture(screen_access.screen_recording_state())
        if permission:
            detail = "Allow Screen Recording for Personal Jarvis in macOS System Settings."
    return {"available": not detail, "detail": detail, "permission_required": permission}


class RecordingService:
    """One owned process; repeated starts cannot create concurrent recorders."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._state: dict[str, Any] = {"phase": "idle", "id": "", "message": ""}
        self._started: float | None = None
        from jarvis.appshot.recording_runtime import RecordingRuntime

        self._runtime = RecordingRuntime()
        self._warm_enabled = False
        self._warm_task: asyncio.Task[None] | None = None
        self._requested: float | None = None

    async def set_warm(self, enabled: bool) -> None:
        """Prepare after boot; disabling/reloading shortcuts never stops an active video."""
        self._warm_enabled = enabled
        if enabled:
            self._schedule_warm()
            return
        task, self._warm_task = self._warm_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._runtime.close()

    def _schedule_warm(self) -> None:
        if self._process is None and (self._warm_task is None or self._warm_task.done()):
            self._warm_task = asyncio.create_task(self._prepare(), name="appshot-recorder-warm")

    async def _prepare(self) -> None:
        try:
            ready = await asyncio.to_thread(capability)
            if ready["available"] or ready.get("permission_required"):
                await self._runtime.prewarm()
        except Exception:
            log.warning(
                "appshot: recorder preparation failed; next start will retry", exc_info=True
            )

    async def close(self) -> None:
        await self.set_warm(False)
        await self.stop()

    def status(self) -> dict[str, Any]:
        state = dict(self._state)
        if state["phase"] == "recording" and self._started is not None:
            state["duration_s"] = round(time.monotonic() - self._started, 1)
        return state

    async def start(self) -> dict[str, Any]:
        from jarvis.core.config import load_config

        async with self._lock:
            if self._process is not None:
                return self.status()
            config = await asyncio.to_thread(load_config)
            if not config.screen_context.enabled:
                raise ValueError("Enable AppShots before starting a screen recording.")
            if sys.platform == "darwin":
                from jarvis.platform.screen_access import (
                    ScreenCaptureRefused,
                    require_screen_recording_async,
                )

                try:
                    await require_screen_recording_async("appshot")
                except ScreenCaptureRefused as exc:
                    raise ValueError(exc.user_detail) from exc
            ready = await asyncio.to_thread(capability)
            if not ready["available"]:
                raise ValueError(ready["detail"])
            recording_id = uuid.uuid4().hex
            folder = recording_dir()
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            self._started = None
            self._state = {"phase": "selecting", "id": recording_id, "message": ""}
            self._requested = time.perf_counter()
            try:
                self._process = await self._runtime.take(
                    folder / f"{recording_id}.mp4", str(getattr(config.ui, "language", "en"))
                )
            except asyncio.CancelledError:
                self._state["phase"] = "cancelled"
                raise
            except (OSError, TimeoutError) as exc:
                self._state.update(phase="error", message="The recorder could not start.")
                log.exception("appshot: recorder spawn failed")
                raise ValueError(self._state["message"]) from exc
            self._reader = asyncio.create_task(self._read(self._process), name="appshot-recording")
            return self.status()

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        preview = None
        try:
            async for raw in process.stdout:
                try:
                    event = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeError):
                    log.debug("appshot: ignoring non-protocol recorder output")
                    continue
                if not isinstance(event, dict):
                    continue
                phase = event.get("phase")
                if phase == "selection_ready" and self._requested is not None:
                    log.info(
                        "appshot: recording picker visible in %.1f ms",
                        (time.perf_counter() - self._requested) * 1000,
                    )
                    continue
                if phase not in {"selecting", "recording", "saved", "cancelled", "error"}:
                    continue
                self._state.update(
                    {
                        key: event[key]
                        for key in ("phase", "message", "width", "height", "duration_s")
                        if key in event
                    }
                )
                if phase == "recording" and self._started is None:
                    self._started = time.monotonic()
                if phase == "saved" and event.get("preview"):
                    preview = event
            code = await process.wait()
            if self._state["phase"] in ACTIVE_PHASES or (
                code != 0 and self._state["phase"] != "error"
            ):
                self._state.update(phase="error", message="The recorder stopped unexpectedly.")
                log.warning("appshot: recorder exited with code %s", code)
            elif preview is not None and self._state["phase"] == "saved":
                from jarvis.appshot.recording_cards import show_recording_preview

                await show_recording_preview(self._state["id"], preview)
        except Exception:
            log.exception("appshot: recorder monitor failed")
            self._state.update(phase="error", message="The recorder connection was lost.")
            if process.returncode is None:
                process.kill()
                await process.wait()
        finally:
            if process.stdin:
                process.stdin.close()
            self._process = None
            self._runtime.release()
            if self._warm_enabled:
                self._schedule_warm()

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            process = self._process
            if process is None:
                return self.status()
            self._state["phase"] = "stopping"
            if process.stdin:
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    process.stdin.write(b"stop\n")
                    await process.stdin.drain()
            if self._reader:
                try:
                    await asyncio.wait_for(asyncio.shield(self._reader), timeout=20)
                except TimeoutError:
                    log.warning("appshot: recorder finalization timed out; terminating sidecar")
                    if process.returncode is None:
                        process.kill()
                    await self._reader
                    self._state.update(
                        phase="error", message="The recording could not finish writing."
                    )
            return self.status()

    async def toggle(self) -> dict[str, Any]:
        if self._process is not None:
            return await self.stop()
        return await self.start()


_service: RecordingService | None = None


def get_recording_service() -> RecordingService:
    global _service
    if _service is None:
        _service = RecordingService()
    return _service


async def warm_recording_service(enabled: bool) -> None:
    if enabled:
        await get_recording_service().set_warm(True)
    elif _service is not None:
        await _service.set_warm(False)


async def close_recording_service() -> None:
    global _service
    service, _service = _service, None
    if service is not None:
        await service.close()
