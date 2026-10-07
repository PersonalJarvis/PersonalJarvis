"""Main-process side of the Jarvis X overlay sidecar.

Spawns ``python -m jarvis.jarvisx.overlay`` on first use, writes commands,
waits for acknowledgements, and routes the sidecar's events (a region was
selected, a card was clicked, the Stop pill was clicked) back to the Jarvis X
service. The sidecar quits itself when it has been idle for a while — no card
on screen, no selection, no recording — and is respawned on the next capture.

Degradations (each logged once, never raised): no display, a Wayland session
or a missing PySide6 mean no overlay — screenshots of a window or a screen
still work (without the flash and card), a region selection reports the
reason instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import itertools
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from jarvis.jarvisx.overlay import protocol

log = logging.getLogger(__name__)

_ACK_TIMEOUT_S = 1.5
_BLANK_ACK_TIMEOUT_S = 0.25
#: A selection nobody finishes is abandoned after this long.
_SELECT_TIMEOUT_S = 300.0
#: How long an idle sidecar (nothing on screen) stays up before quitting.
_IDLE_QUIT_S = 90.0
_QUIT_GRACE_S = 1.5
#: Qt start-up budget of a fresh sidecar (cold disk, first PySide6 import).
_STARTUP_S = 8.0

# Overlay copy for every supported interface language ([ui].language).
_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "select_capture": "Drag to capture  ·  Esc to cancel",
        "select_record": "Drag the area to record  ·  Esc to cancel",
        "edit": "Click to edit",
        "stop": "Stop",
    },
    "de": {
        "select_capture": "Bereich aufziehen  ·  Esc bricht ab",  # i18n-allow: overlay UI
        "select_record": "Aufnahmebereich aufziehen  ·  Esc bricht ab",  # i18n-allow: overlay UI
        "edit": "Zum Bearbeiten klicken",  # i18n-allow: localized overlay UI string
        "stop": "Stopp",  # i18n-allow: localized overlay UI string
    },
    "es": {
        "select_capture": "Arrastra para capturar  ·  Esc para cancelar",  # i18n-allow: overlay UI
        "select_record": "Arrastra el área a grabar  ·  Esc para cancelar",  # i18n-allow: UI
        "edit": "Haz clic para editar",  # i18n-allow: localized overlay UI string
        "stop": "Detener",  # i18n-allow: localized overlay UI string
    },
    "zh": {
        "select_capture": "拖动选择截图区域  ·  按 Esc 取消",  # i18n-allow: product UI string
        "select_record": "拖动选择录制区域  ·  按 Esc 取消",  # i18n-allow: product UI string
        "edit": "点击编辑",  # i18n-allow: product UI string
        "stop": "停止",  # i18n-allow: product UI string
    },
}


def texts_for(language: str) -> dict[str, str]:
    return dict(_TEXTS.get(str(language or "en"), _TEXTS["en"]))


def _ui_texts() -> dict[str, str]:
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        return texts_for(str(getattr(load_config().ui, "language", "en")))
    except Exception:  # noqa: BLE001 - English is always a correct fallback
        log.debug("jarvisx: interface language unavailable", exc_info=True)
        return texts_for("en")


def overlay_capability() -> tuple[bool, str]:
    """Whether the overlay can run here, and the reason when it cannot."""
    try:
        from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

        if not display_present():
            return False, "no display on this host (headless)"
        if is_wayland():
            return False, "Wayland session (no always-on-top overlay surface)"
    except Exception:  # noqa: BLE001  # Return the unavailable capability and reason to the UI.
        return False, "platform probes unavailable"
    if importlib.util.find_spec("PySide6") is None:
        return False, "PySide6 is not installed (install the [desktop] extra)"
    return True, ""


@dataclass(frozen=True, slots=True)
class Selection:
    """A finished region selection: which Qt screen, which part of it."""

    screen: dict[str, float]
    rect: tuple[float, float, float, float]


class SelectionUnavailable(RuntimeError):
    """No selection overlay can run here; the message says why."""


EventHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class OverlayController:
    """Owns the sidecar process and the request/response plumbing."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._stdin_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._acks: queue.Queue[str] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handler: EventHandler | None = None
        self._selections: dict[int, asyncio.Future[Selection | None]] = {}
        self._req_ids = itertools.count(1)
        self._warned = False
        self._cards: set[str] = set()
        self._recording = False
        self._last_activity = time.monotonic()
        self._idle_task: asyncio.Task[None] | None = None
        self._spawned_at = 0.0

    # ----------------------------------------------------------------- setup
    def bind(self, loop: asyncio.AbstractEventLoop, handler: EventHandler) -> None:
        """Route sidecar events (card clicks, stop clicks) to ``handler``."""
        self._loop = loop
        self._handler = handler

    @property
    def available(self) -> bool:
        return overlay_capability()[0]

    # ------------------------------------------------------------- commands
    async def select_region(self, *, purpose: str) -> Selection | None:
        """Let the user drag a rectangle. ``None`` = cancelled.

        Raises :class:`SelectionUnavailable` when no overlay can run here.
        """
        ok, reason = overlay_capability()
        if not ok:
            raise SelectionUnavailable(f"A region cannot be selected here: {reason}.")
        loop = asyncio.get_running_loop()
        self._loop = self._loop or loop
        await asyncio.to_thread(self._spawn)
        if self._proc is None:
            raise SelectionUnavailable("The selection overlay could not be started.")
        req = next(self._req_ids)
        future: asyncio.Future[Selection | None] = loop.create_future()
        self._selections[req] = future
        texts = _ui_texts()
        hint = texts["select_record" if purpose == "record" else "select_capture"]
        sent = await asyncio.to_thread(
            self._send_and_wait,
            protocol.CMD_SELECT,
            _ACK_TIMEOUT_S,
            req=req,
            purpose=purpose,
            hint=hint,
            texts=texts,
        )
        if not sent:
            self._selections.pop(req, None)
            raise SelectionUnavailable("The selection overlay did not respond.")
        escape = asyncio.create_task(self._escape_cancels(req), name="jarvisx-select-esc")
        try:
            return await asyncio.wait_for(future, timeout=_SELECT_TIMEOUT_S)
        except TimeoutError:  # Cancel the expired selection and report no chosen region.
            await asyncio.to_thread(self._send_and_wait, protocol.CMD_CANCEL_SELECT, _ACK_TIMEOUT_S)
            return None
        finally:
            self._selections.pop(req, None)
            escape.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await escape
            self._touch()

    async def _escape_cancels(self, req: int) -> None:
        """A global Escape also cancels: the overlay may not get keyboard focus.

        Windows refuses to hand the foreground to a background process, so the
        overlay's own Esc handler can be deaf; the shared hotkey backend is not.
        """
        try:
            from jarvis.platform.probes import has_hotkey  # noqa: PLC0415

            if not has_hotkey():
                return
            from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

            key = "escape" if sys.platform == "win32" else "esc"
            trigger = HotkeyTrigger({"jarvisx_cancel": [key]})
            async with trigger:
                async for name in trigger.events():
                    if name == "jarvisx_cancel" and req in self._selections:
                        await asyncio.to_thread(
                            self._send_and_wait, protocol.CMD_CANCEL_SELECT, _ACK_TIMEOUT_S
                        )
                        return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the overlay's own Esc and right-click still cancel
            log.debug("jarvisx: global Escape for the selection unavailable", exc_info=True)

    async def show_card(
        self,
        *,
        item_id: str,
        monitor: list[int],
        rect: list[float] | None,
        thumb_b64: str,
        persist: bool,
        dismiss_s: int,
        badge: str = "",
    ) -> bool:
        if not self.available:
            return False
        await asyncio.to_thread(self._spawn)
        if self._proc is None:
            return False
        shown = await asyncio.to_thread(
            self._send_and_wait,
            protocol.CMD_CARD,
            _ACK_TIMEOUT_S,
            id=item_id,
            monitor=list(monitor),
            rect=list(rect) if rect is not None else None,
            thumb=thumb_b64,
            persist=bool(persist),
            dismiss_s=int(dismiss_s),
            badge=badge,
            texts=_ui_texts(),
        )
        if shown:
            self._cards.add(item_id)
        self._touch()
        return shown

    async def remove_card(self, item_id: str) -> None:
        self._cards.discard(item_id)
        if self._running():
            await asyncio.to_thread(
                self._send_and_wait, protocol.CMD_REMOVE_CARD, _ACK_TIMEOUT_S, id=item_id
            )

    async def show_recording(self, *, monitor: list[int], rect: list[float] | None) -> bool:
        if not self.available:
            return False
        await asyncio.to_thread(self._spawn)
        if self._proc is None:
            return False
        self._recording = True
        return await asyncio.to_thread(
            self._send_and_wait,
            protocol.CMD_REC_SHOW,
            _ACK_TIMEOUT_S,
            monitor=list(monitor),
            rect=list(rect) if rect is not None else None,
            texts=_ui_texts(),
        )

    async def hide_recording(self) -> None:
        self._recording = False
        self._touch()
        if self._running():
            await asyncio.to_thread(self._send_and_wait, protocol.CMD_REC_HIDE, _ACK_TIMEOUT_S)

    @contextlib.contextmanager
    def cards_hidden(self) -> Iterator[None]:
        """Hide the resting cards around one screenshot (blocking; worker thread).

        Fail-open: a dead or slow sidecar costs at most the short ack wait,
        never the capture itself.
        """
        blanked = (
            self._running()
            and bool(self._cards)
            and self._send_and_wait(protocol.CMD_BLANK, _BLANK_ACK_TIMEOUT_S)
        )
        try:
            if blanked:
                # One compositor frame so the hidden cards are off the glass.
                time.sleep(0.05)
            yield
        finally:
            if blanked:
                self._send_and_wait(protocol.CMD_UNBLANK, _BLANK_ACK_TIMEOUT_S)

    async def shutdown(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
        await asyncio.to_thread(self._quit)

    # -------------------------------------------------------------- process
    def _running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _spawn(self) -> None:
        if self._running():
            return
        try:
            from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

            self._proc = subprocess.Popen(
                [sys.executable, "-m", "jarvis.jarvisx.overlay"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=NO_WINDOW_CREATIONFLAGS,
                env=os.environ.copy(),
            )
        except Exception:  # noqa: BLE001 - reported once; captures still work
            self._proc = None
            if not self._warned:
                self._warned = True
                log.warning("jarvisx: overlay sidecar could not start", exc_info=True)
            return
        self._cards.clear()
        self._spawned_at = time.monotonic()
        threading.Thread(
            target=self._pump, args=(self._proc,), name="jarvisx-overlay-out", daemon=True
        ).start()
        self._schedule_idle_quit()

    def _pump(self, proc: subprocess.Popen[str]) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                reply = protocol.decode_reply(line)
                if reply is None:
                    continue
                kind, payload = reply
                if kind == "ack":
                    self._acks.put(str(payload["ok"]))
                else:
                    self._dispatch(payload)
        except Exception:  # noqa: BLE001 - the pipe closes when the sidecar exits
            log.debug("jarvisx: overlay output closed", exc_info=True)
        finally:
            self._dispatch({"event": "_exited"})

    def _dispatch(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._on_event, payload)

    def _on_event(self, payload: dict[str, Any]) -> None:
        """Runs on the event loop."""
        event = str(payload.get("event", ""))
        self._touch()
        if event == protocol.EVENT_SELECTION:
            future = self._selections.get(int(payload.get("req", -1) or -1))
            if future is not None and not future.done():
                future.set_result(_selection_from(payload))
            return
        if event == "_exited":
            # The sidecar died: fail every open selection as "cancelled".
            for future in self._selections.values():
                if not future.done():
                    future.set_result(None)
            self._cards.clear()
            return
        if event in (protocol.EVENT_CARD_CLICK, protocol.EVENT_CARD_CLOSED):
            self._cards.discard(str(payload.get("id", "")))
        handler = self._handler
        if handler is None:
            return
        try:
            result = handler(event, payload)
            if asyncio.iscoroutine(result):
                task = asyncio.get_running_loop().create_task(result, name=f"jarvisx-{event}")
                task.add_done_callback(_log_task_failure)
        except Exception:  # noqa: BLE001 - one bad handler must not stop the pump
            log.warning("jarvisx: overlay event handler failed (%s)", event, exc_info=True)

    def _send(self, cmd: str, **fields: Any) -> bool:
        try:
            with self._stdin_lock:
                proc = self._proc
                if proc is None or proc.stdin is None or proc.poll() is not None:
                    return False
                proc.stdin.write(protocol.encode_command(cmd, **fields))
                proc.stdin.flush()
            return True
        except Exception:  # noqa: BLE001 - a broken pipe means the sidecar is gone
            log.info("jarvisx: overlay pipe broke; it restarts on the next capture")
            with self._stdin_lock:
                self._proc = None
            return False

    def _send_and_wait(self, cmd: str, timeout_s: float, **fields: Any) -> bool:
        with self._command_lock:
            while True:
                try:
                    self._acks.get_nowait()
                except queue.Empty:  # stale acks drained
                    break
            if not self._send(cmd, **fields):
                return False
            # A freshly spawned sidecar first has to bring Qt up; its first
            # commands wait in the pipe meanwhile.
            startup_left = self._spawned_at + _STARTUP_S - time.monotonic()
            try:
                return self._acks.get(timeout=max(timeout_s, startup_left)) == cmd
            except queue.Empty:  # a slow sidecar is reported as "not done"
                return False

    def _quit(self) -> None:
        if not self._running():
            return
        self._send_and_wait(protocol.CMD_QUIT, _ACK_TIMEOUT_S)
        with self._stdin_lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.wait(timeout=_QUIT_GRACE_S)
        except Exception:  # noqa: BLE001 - escalate to a kill
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(timeout=1.0)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                with contextlib.suppress(Exception):
                    pipe.close()

    # ----------------------------------------------------------------- idle
    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    def _idle(self) -> bool:
        return (
            not self._cards
            and not self._recording
            and not self._selections
            and time.monotonic() - self._last_activity >= _IDLE_QUIT_S
        )

    def _schedule_idle_quit(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        task = self._idle_task
        if task is not None and not task.done():
            return

        def _start() -> None:
            self._idle_task = asyncio.get_running_loop().create_task(
                self._idle_watch(), name="jarvisx-overlay-idle"
            )

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_start)

    async def _idle_watch(self) -> None:
        # Cards that time out inside the sidecar are not reported back, so
        # the card set is only an upper bound; a persistent card keeps the
        # sidecar alive until the user closes or clicks it.
        while self._running():
            await asyncio.sleep(15.0)
            if self._idle():
                await asyncio.to_thread(self._quit)
                return


def _selection_from(payload: dict[str, Any]) -> Selection | None:
    if payload.get("cancelled"):
        return None
    screen = payload.get("screen")
    rect = payload.get("rect")
    if not isinstance(screen, dict) or not isinstance(rect, list) or len(rect) != 4:
        return None
    try:
        values = tuple(float(v) for v in rect)
        info = {k: float(screen.get(k, 0.0)) for k in ("x", "y", "w", "h", "dpr")}
    except (TypeError, ValueError):  # Reject malformed screen geometry from the helper.
        return None
    return Selection(screen=info, rect=(values[0], values[1], values[2], values[3]))


def _log_task_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("jarvisx: overlay event handling failed", exc_info=exc)


_controller: OverlayController | None = None


def get_overlay() -> OverlayController:
    global _controller
    if _controller is None:
        _controller = OverlayController()
    return _controller


__all__ = [
    "OverlayController",
    "Selection",
    "SelectionUnavailable",
    "get_overlay",
    "overlay_capability",
    "texts_for",
]
