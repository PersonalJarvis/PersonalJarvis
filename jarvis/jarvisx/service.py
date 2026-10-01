"""The Jarvis X orchestrator — every trigger (shortcut, REST, card click) ends here.

One capture at a time, one recording at a time. A screenshot runs:

1. resolve the area — a dragged region (overlay), the front window, or the
   monitor under the pointer;
2. grab it with the resting thumbnail cards hidden;
3. save the PNG to the save folder, a preview to the library, index it;
4. play the capture cue, show the flash + corner card, copy to the clipboard
   (each behind its own switch);
5. publish ``JarvisXItemCreated``.

A recording starts the same way for the area, then runs the recorder until a
stop (shortcut, pill click or REST) and lands in the library like a picture.

Every public coroutine returns a result object and never raises: a shortcut
press must never take the app down.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from jarvis.jarvisx import capture, geometry, paths
from jarvis.jarvisx.store import Item, ItemStore, get_store

log = logging.getLogger(__name__)

CaptureMode = Literal["region", "window", "fullscreen"]
RecordMode = Literal["region", "fullscreen"]

#: Largest annotated PNG the editor may save.
MAX_EDITED_BYTES = 80 * 1024 * 1024

#: Pause after the selection overlay closed, so the compositor has removed
#: the dimmed layer from the glass before the grab.
_AFTER_SELECTION_S = 0.12


@dataclass(frozen=True, slots=True)
class Result:
    ok: bool
    message: str = ""
    item: Item | None = None


@dataclass
class _ActiveRecording:
    recorder: Any
    mode: RecordMode
    monitor: dict
    rect: list[float] | None


def _load_config() -> Any:
    from jarvis.core.config import load_config  # noqa: PLC0415

    return load_config()


def _now() -> datetime:
    return datetime.now().astimezone()


class JarvisXService:
    """Capture, record and library actions. Obtain it with :func:`get_service`."""

    def __init__(
        self,
        *,
        store: ItemStore | None = None,
        overlay: Any | None = None,
        config_loader: Callable[[], Any] = _load_config,
    ) -> None:
        self._store = store
        self._overlay = overlay
        self._config_loader = config_loader
        self._bus: Any | None = None
        self._app_state: Any | None = None
        self._busy = asyncio.Lock()
        self._recording: _ActiveRecording | None = None
        self._record_lock = asyncio.Lock()
        self._bound_loop: asyncio.AbstractEventLoop | None = None

    # ---------------------------------------------------------------- wiring
    def bind(self, *, bus: Any | None = None, app_state: Any | None = None) -> None:
        """Attach the event bus and the web app state (for the editor window)."""
        if bus is not None:
            self._bus = bus
        if app_state is not None:
            self._app_state = app_state
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            if self._bound_loop is not loop:
                self.overlay.bind(loop, self._on_overlay_event)
                self._bound_loop = loop

    @property
    def store(self) -> ItemStore:
        return self._store if self._store is not None else get_store()

    @property
    def overlay(self) -> Any:
        if self._overlay is None:
            from jarvis.jarvisx.overlay.controller import get_overlay  # noqa: PLC0415

            self._overlay = get_overlay()
        return self._overlay

    async def _config(self) -> Any:
        return await asyncio.to_thread(self._config_loader)

    # --------------------------------------------------------------- capture
    async def capture(self, mode: CaptureMode, *, delay_s: float = 0.0) -> Result:
        """Take one screenshot. Never raises."""
        self.bind()
        if self._busy.locked():
            return Result(False, "A capture is already in progress.")
        async with self._busy:
            try:
                return await self._capture(mode, delay_s)
            except Exception:  # noqa: BLE001 - a shortcut press must never crash the app
                log.error("jarvisx: capture failed", exc_info=True)
                return Result(False, "The capture failed. Nothing was saved.")

    async def _capture(self, mode: CaptureMode, delay_s: float) -> Result:
        config = await self._config()
        block = config.jarvisx
        if not block.enabled:
            return Result(False, "Jarvis X is switched off. Turn it on in its settings.")
        refusal = await asyncio.to_thread(_capture_refusal)
        if refusal:
            return Result(False, refusal)
        if delay_s > 0:
            await asyncio.sleep(min(float(delay_s), 10.0))

        monitors = await asyncio.to_thread(capture.list_monitors)
        if not monitors:
            return Result(False, "No screen could be found to capture.")
        handle: int | None = None
        if mode == "region":
            area = await self._select_area(monitors, purpose="capture")
            if isinstance(area, str):
                return Result(False, area)
            monitor, bbox, rect = area
        elif mode == "window":
            try:
                bbox, handle, _title = await asyncio.to_thread(capture.front_window)
            except capture.CaptureError as exc:
                return Result(False, str(exc))
            from jarvis.appshot.effect import placement  # noqa: PLC0415

            mon_rect, frac = placement(bbox, monitors)
            monitor = dict(zip(("left", "top", "width", "height"), mon_rect, strict=True))
            rect = list(frac)
        else:
            found = await asyncio.to_thread(capture.monitor_under_cursor, monitors)
            if found is None:
                return Result(False, "No screen could be found to capture.")
            monitor = found
            bbox = tuple(geometry.monitor_rect(found))  # type: ignore[assignment]
            rect = [0.0, 0.0, 1.0, 1.0]

        try:
            frame = await asyncio.to_thread(self._grab, mode, bbox, handle)
        except capture.CaptureError as exc:
            return Result(False, str(exc))

        item, png = await asyncio.to_thread(self._save_image, mode, frame, block.save_dir)
        self._after_capture(config, item, monitor, rect, png=png)
        await self._publish_created(item)
        log.info("jarvisx: %s screenshot %dx%d saved", mode, item.width, item.height)
        return Result(True, "Saved.", item)

    def _grab(self, mode: str, bbox: Any, handle: int | None) -> capture.Frame:
        with self.overlay.cards_hidden():
            if mode == "window":
                return capture.grab_window(bbox, handle)
            return capture.grab_rect(bbox)

    async def _select_area(
        self, monitors: list[dict], *, purpose: str
    ) -> tuple[dict, tuple[int, int, int, int], list[float]] | str:
        """The dragged area as ``(monitor, bbox, fractions)`` or a message."""
        from jarvis.jarvisx.overlay.controller import SelectionUnavailable  # noqa: PLC0415

        try:
            selection = await self.overlay.select_region(purpose=purpose)
        except SelectionUnavailable as exc:
            return str(exc)
        if selection is None:
            return "Cancelled."
        monitor = geometry.match_monitor(selection.screen, monitors)
        if monitor is None:
            return "The selected screen could not be found."
        bbox = geometry.fraction_to_bbox(monitor, selection.rect)
        await asyncio.sleep(_AFTER_SELECTION_S)
        return monitor, bbox, list(selection.rect)

    def _save_image(self, mode: str, frame: capture.Frame, save_dir: str) -> tuple[Item, bytes]:
        png = capture.encode_png(frame)
        when = _now()
        folder = paths.resolve_save_dir(save_dir)
        target = paths.unique_path(folder, paths.capture_filename("image", when, "png"))
        _atomic_write(target, png)
        item_id = uuid.uuid4().hex
        thumb_path = self.store.thumb_path_for(item_id)
        try:
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            thumb_path.write_bytes(capture.thumbnail_jpeg(frame.width, frame.height, frame.rgb))
        except OSError:
            log.warning("jarvisx: preview could not be written", exc_info=True)
        item = Item(
            id=item_id,
            kind="image",
            mode=mode,  # type: ignore[arg-type]
            created_at=when.isoformat(timespec="seconds"),
            width=frame.width,
            height=frame.height,
            duration_s=None,
            path=str(target),
            thumb_path=str(thumb_path),
        )
        return self.store.add(item), png

    def _after_capture(
        self,
        config: Any,
        item: Item,
        monitor: dict,
        rect: list[float] | None,
        *,
        png: bytes | None,
        badge: str = "",
    ) -> None:
        """Sound, card and clipboard — each optional, none can fail the capture."""
        block = config.jarvisx
        if block.sound:
            self._play_cue()
        if block.effect:
            self._spawn(self._show_card(config, item, monitor, rect, badge), "jarvisx-card")
        if png is not None and block.copy_to_clipboard:
            self._spawn(self._copy_bytes(png), "jarvisx-clipboard")

    def _play_cue(self) -> None:
        try:
            from jarvis.audio.effects import get_audio_effects_service  # noqa: PLC0415

            get_audio_effects_service().play_capture_cue(section="jarvisx")
        except Exception:  # noqa: BLE001 - audio is decoration
            log.info("jarvisx: capture cue unavailable", exc_info=True)

    async def _show_card(
        self, config: Any, item: Item, monitor: dict, rect: list[float] | None, badge: str
    ) -> None:
        try:
            thumb = await asyncio.to_thread(_read_bytes, item.thumb_path)
            await self.overlay.show_card(
                item_id=item.id,
                monitor=geometry.monitor_rect(monitor),
                rect=rect,
                thumb_b64=base64.b64encode(thumb).decode("ascii") if thumb else "",
                persist=bool(config.jarvisx.thumbnail_persist),
                dismiss_s=geometry.clamp_dismiss_s(config.jarvisx.thumbnail_dismiss_s),
                badge=badge,
            )
        except Exception:  # noqa: BLE001 - the card is decoration
            log.warning("jarvisx: thumbnail card failed", exc_info=True)

    async def _copy_bytes(self, png: bytes) -> tuple[bool, str]:
        from jarvis.jarvisx.clipboard import copy_png  # noqa: PLC0415

        ok, message = await asyncio.to_thread(copy_png, png)
        if not ok:
            log.info("jarvisx: clipboard copy skipped: %s", message)
        return ok, message

    # ------------------------------------------------------------- recording
    @property
    def recording(self) -> bool:
        return self._recording is not None

    def recording_status(self) -> dict[str, Any]:
        active = self._recording
        if active is None:
            return {"recording": False, "mode": None, "elapsed_s": 0.0}
        return {
            "recording": True,
            "mode": active.mode,
            "elapsed_s": round(float(active.recorder.elapsed_s), 2),
        }

    async def start_recording(self, mode: RecordMode) -> Result:
        """Start recording the chosen area. Never raises."""
        self.bind()
        async with self._record_lock:
            if self._recording is not None:
                return Result(False, "A recording is already running.")
            if self._busy.locked():
                return Result(False, "A capture is already in progress.")
            async with self._busy:
                try:
                    return await self._start_recording(mode)
                except Exception:  # noqa: BLE001 - a shortcut press must never crash the app
                    log.error("jarvisx: recording could not start", exc_info=True)
                    return Result(False, "The recording could not start.")

    async def _start_recording(self, mode: RecordMode) -> Result:
        from jarvis.jarvisx import recorder as rec  # noqa: PLC0415

        config = await self._config()
        block = config.jarvisx
        if not block.enabled:
            return Result(False, "Jarvis X is switched off. Turn it on in its settings.")
        refusal = await asyncio.to_thread(_capture_refusal)
        if refusal:
            return Result(False, refusal)
        choice = await asyncio.to_thread(rec.probe_encoder)
        if not choice.available:
            return Result(False, choice.detail)
        monitors = await asyncio.to_thread(capture.list_monitors)
        if not monitors:
            return Result(False, "No screen could be found to record.")
        rect: list[float] | None
        if mode == "region":
            area = await self._select_area(monitors, purpose="record")
            if isinstance(area, str):
                return Result(False, area)
            monitor, bbox, rect = area
        else:
            found = await asyncio.to_thread(capture.monitor_under_cursor, monitors)
            if found is None:
                return Result(False, "No screen could be found to record.")
            monitor = found
            bbox = tuple(geometry.monitor_rect(found))  # type: ignore[assignment]
            rect = None
        folder = await asyncio.to_thread(paths.resolve_save_dir, block.save_dir)
        target = paths.unique_path(
            folder, paths.capture_filename("video", _now(), choice.extension)
        )
        recorder = self._make_recorder(choice, bbox, target)
        error = await asyncio.to_thread(recorder.start)
        if error:
            await asyncio.to_thread(_unlink, target)
            return Result(False, error)
        self._recording = _ActiveRecording(recorder=recorder, mode=mode, monitor=monitor, rect=rect)
        await self.overlay.show_recording(monitor=geometry.monitor_rect(monitor), rect=rect)
        if block.sound:
            self._play_cue()
        await self._publish_recording(True, mode, 0.0)
        log.info("jarvisx: %s recording started -> %s", mode, target.name)
        return Result(True, "Recording.")

    def _make_recorder(self, choice: Any, bbox: Any, target: Path) -> Any:
        from jarvis.jarvisx import recorder as rec  # noqa: PLC0415

        area = tuple(int(v) for v in bbox)
        return rec.Recorder(
            source_factory=lambda: rec.MssFrameSource(area),  # type: ignore[arg-type]
            encoder=rec.PyAvEncoder(choice),
            path=target,
        )

    async def stop_recording(self) -> Result:
        """Stop and save the running recording. Never raises."""
        async with self._record_lock:
            active, self._recording = self._recording, None
            if active is None:
                return Result(False, "No recording is running.")
            try:
                return await self._finish_recording(active)
            except Exception:  # noqa: BLE001 - reported, the app keeps running
                log.error("jarvisx: recording could not be saved", exc_info=True)
                await self._publish_recording(False, "", 0.0)
                return Result(False, "The recording could not be saved.")

    async def _finish_recording(self, active: _ActiveRecording) -> Result:
        await self.overlay.hide_recording()
        result = await asyncio.to_thread(active.recorder.stop)
        await self._publish_recording(False, "", float(result.duration_s))
        if result.frames <= 0 or not result.path.is_file():
            await asyncio.to_thread(_unlink, result.path)
            return Result(False, result.error or "Nothing was recorded.")
        item = await asyncio.to_thread(self._index_video, active, result)
        config = await self._config()
        self._after_capture(
            config,
            item,
            active.monitor,
            active.rect,
            png=None,
            badge=geometry.format_elapsed(result.duration_s),
        )
        await self._publish_created(item)
        log.info(
            "jarvisx: recording saved (%.1f s, %d frames) %s",
            result.duration_s,
            result.frames,
            result.path.name,
        )
        message = "Saved." if not result.error else f"Saved, but: {result.error}"
        return Result(True, message, item)

    def _index_video(self, active: _ActiveRecording, result: Any) -> Item:
        item_id = uuid.uuid4().hex
        thumb_path = self.store.thumb_path_for(item_id)
        if result.first_frame is not None:
            try:
                width, height, bgra = result.first_frame
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                thumb_path.write_bytes(_bgra_thumbnail(width, height, bgra))
            except Exception:  # noqa: BLE001 - a missing preview is not a lost video
                log.warning("jarvisx: video preview could not be written", exc_info=True)
        item = Item(
            id=item_id,
            kind="video",
            mode=active.mode,
            created_at=_now().isoformat(timespec="seconds"),
            width=int(result.width),
            height=int(result.height),
            duration_s=float(result.duration_s),
            path=str(result.path),
            thumb_path=str(thumb_path),
        )
        return self.store.add(item)

    async def toggle_recording(self, mode: RecordMode) -> Result:
        """A record shortcut: start, or stop when a recording is running."""
        if self._recording is not None:
            return await self.stop_recording()
        return await self.start_recording(mode)

    # --------------------------------------------------------------- library
    async def save_edited(self, item_id: str, png: bytes) -> Result:
        item = await asyncio.to_thread(self.store.get, item_id)
        if item is None:
            return Result(False, "That capture no longer exists.")
        if item.kind != "image":
            return Result(False, "Only screenshots can be annotated.")
        target = paths.edited_path_for(Path(item.path))
        await asyncio.to_thread(_atomic_write, target, png)
        updated = await asyncio.to_thread(self.store.set_edited, item_id, str(target))
        await self._publish(_event("JarvisXItemUpdated", id=item_id))
        return Result(True, "Saved.", updated)

    async def copy_item(self, item_id: str, *, edited: bool) -> Result:
        item = await asyncio.to_thread(self.store.get, item_id)
        if item is None:
            return Result(False, "That capture no longer exists.")
        if item.kind != "image":
            return Result(False, "Only screenshots can be copied as a picture.")
        source = item.edited_path if edited and item.has_edited() else item.path
        try:
            png = await asyncio.to_thread(Path(source).read_bytes)
        except OSError as exc:
            return Result(False, f"The file could not be read ({exc.strerror or exc}).")
        ok, message = await self._copy_bytes(png)
        return Result(ok, message, item)

    async def reveal(self, item_id: str) -> Result:
        item = await asyncio.to_thread(self.store.get, item_id)
        if item is None:
            return Result(False, "That capture no longer exists.")
        from jarvis.platform.open_path import reveal_in_folder  # noqa: PLC0415

        opened = await asyncio.to_thread(reveal_in_folder, Path(item.path))
        if not opened:
            return Result(False, "The file manager could not be opened on this computer.", item)
        return Result(True, "Opened.", item)

    async def delete(self, item_id: str) -> Result:
        item = await asyncio.to_thread(self.store.delete, item_id)
        if item is None:
            return Result(False, "That capture no longer exists.")
        with contextlib.suppress(Exception):
            await self.overlay.remove_card(item_id)
        await self._publish(_event("JarvisXItemDeleted", id=item_id))
        return Result(True, "Deleted.", item)

    async def open_editor(self, item_id: str) -> Result:
        """Open the annotation editor window on ``item_id`` (desktop app only)."""
        item = await asyncio.to_thread(self.store.get, item_id)
        if item is None:
            return Result(False, "That capture no longer exists.")
        desktop = getattr(self._app_state, "desktop_app", None) if self._app_state else None
        opener = getattr(desktop, "open_jarvisx_editor", None)
        if not callable(opener):
            return Result(
                False,
                "The editor window needs the desktop app; this Jarvis runs without one. "
                f"Open /?view=jarvisx-editor&solo=1&item={item_id} in a browser instead.",
                item,
            )
        # pywebview creates runtime windows only off the main thread, and every
        # window call blocks its caller until the GUI thread services it.
        outcome = await asyncio.to_thread(opener, item_id)
        if isinstance(outcome, dict) and not outcome.get("ok", False):
            return Result(False, str(outcome.get("reason") or "The editor could not open."), item)
        return Result(True, "Opened.", item)

    # ---------------------------------------------------------- overlay I/O
    async def _on_overlay_event(self, event: str, payload: dict[str, Any]) -> None:
        if event == "card_click":
            result = await self.open_editor(str(payload.get("id", "")))
            if not result.ok:
                log.info("jarvisx: editor not opened: %s", result.message)
        elif event == "stop_clicked":
            await self.stop_recording()

    # --------------------------------------------------------------- events
    async def _publish_created(self, item: Item) -> None:
        await self._publish(
            _event("JarvisXItemCreated", id=item.id, kind=item.kind, mode=item.mode)
        )

    async def _publish_recording(self, recording: bool, mode: str, elapsed_s: float) -> None:
        await self._publish(
            _event(
                "JarvisXRecordingChanged",
                recording=recording,
                mode=mode,
                elapsed_s=round(float(elapsed_s), 2),
            )
        )

    async def _publish(self, event: Any) -> None:
        bus = self._bus
        if bus is None or event is None:
            return
        try:
            await bus.publish(event)
        except Exception:  # noqa: BLE001 - a lost receipt cannot undo the capture
            log.warning("jarvisx: event publication failed", exc_info=True)

    @staticmethod
    def _spawn(coro: Any, name: str) -> None:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    async def shutdown(self) -> None:
        """Finish a running recording and close the overlay (app quit)."""
        if self._recording is not None:
            await self.stop_recording()
        if self._overlay is not None:
            await self._overlay.shutdown()


_TASKS: set[asyncio.Task[Any]] = set()


def _event(name: str, **fields: Any) -> Any:
    from jarvis.core import events  # noqa: PLC0415

    return getattr(events, name)(source_layer="jarvisx", **fields)


def _capture_refusal() -> str:
    ok, reason = capture.capability()
    if not ok:
        return reason
    return capture.permission_problem()


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _read_bytes(path: str) -> bytes:
    try:
        return Path(path).read_bytes() if path else b""
    except OSError:
        return b""


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("jarvisx: could not remove %s", path, exc_info=True)


def _bgra_thumbnail(width: int, height: int, bgra: bytes) -> bytes:
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    image = Image.frombuffer("RGB", (width, height), bytes(bgra), "raw", "BGRX", 0, 1)
    image.thumbnail((560, 560), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=84)
    return buffer.getvalue()


_service: JarvisXService | None = None


def get_service() -> JarvisXService:
    global _service
    if _service is None:
        _service = JarvisXService()
    return _service


def set_service_for_tests(service: JarvisXService | None) -> None:
    global _service
    _service = service


__all__ = ["JarvisXService", "Result", "get_service", "set_service_for_tests"]
