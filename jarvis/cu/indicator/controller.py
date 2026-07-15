"""Main-process controller for the Computer-Use screen indicator.

Wired once at boot (``wire_cu_indicator(bus)`` from the brain factory,
right where the ComputerUseContext is set). Boot cost is a bus
subscription and nothing else (AP-26): the PySide6 sidecar is spawned
lazily when the FIRST Computer-Use mission starts and terminated when the
LAST one ends (missions can overlap — the controller refcounts
``CUControlStarted``/``CUControlEnded`` pairs).

While at least one mission runs, a global Escape listener is armed
through the existing cross-platform ``HotkeyTrigger`` backends; a real
(non-Jarvis-synthesized, see ``self_input``) Escape press cancels EVERY
active mission through the same CU-scoped token registry the voice hangup
uses — the engine exits with code 130 and reports an honest cancel.

Degradations (each logs one English line, never crashes, never blocks a
mission): headless / Wayland / PySide6 missing → no border; hotkey
backend unavailable (Wayland, missing macOS permission) → no Escape and
therefore no "Esc to cancel" pill (the border must not promise a key that
cannot work).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Any

from jarvis.cu.indicator import capture_guard, protocol, self_input
from jarvis.cu.indicator.win32 import capture_exclusion_available

log = logging.getLogger(__name__)

# "Esc to cancel" pill copy — ALL supported output locales (repo rule:
# a phrase table always carries every supported language and resolves its
# key through the one turn-language resolver).
_ESC_HINTS: dict[str, str] = {
    "de": "Esc zum Abbrechen",  # i18n-allow: localized product-surface UI string
    "en": "Esc to cancel",
    "es": "Esc para cancelar",  # i18n-allow: localized product-surface UI string
}

_QUIT_GRACE_S = 1.5
_BLANK_ACK_TIMEOUT_S = 0.15


def _screen_indicator_enabled() -> bool:
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        cu = getattr(load_config(), "computer_use", None)
        return bool(getattr(cu, "screen_indicator", True))
    except Exception:  # noqa: BLE001 — config trouble must not break missions
        return True


def _resolve_hint_language() -> str:
    """Pill language: reply-language pin → DEFAULT_LOCALE (honesty rule —
    this layer has no turn text, so it never guesses beyond the resolver)."""
    from jarvis.core.turn_language import (  # noqa: PLC0415
        DEFAULT_LOCALE,
        resolve_output_language,
    )

    pin = ""
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        pin = str(getattr(load_config().brain, "reply_language", "") or "")
    except Exception:  # noqa: BLE001
        pin = ""
    return resolve_output_language(pin, "", "", default=DEFAULT_LOCALE)


class CUIndicatorController:
    """Refcounts CU missions; owns the sidecar process + Escape listener."""

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._active = 0
        self._lock = asyncio.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._stdin_lock = threading.Lock()
        self._acks: queue.Queue[str] = queue.Queue()
        self._esc_task: asyncio.Task | None = None
        self._sidecar_warned = False

    # ------------------------------------------------------------------ wiring
    def wire(self) -> None:
        from jarvis.core.events import (  # noqa: PLC0415
            CUControlEnded,
            CUControlStarted,
        )

        self._bus.subscribe(CUControlStarted, self._on_started)
        self._bus.subscribe(CUControlEnded, self._on_ended)

    async def _on_started(self, event: Any) -> None:
        del event
        async with self._lock:
            self._active += 1
            if self._active == 1:
                await self._activate()

    async def _on_ended(self, event: Any) -> None:
        del event
        async with self._lock:
            if self._active == 0:
                return
            self._active -= 1
            if self._active == 0:
                await self._deactivate()

    # ------------------------------------------------------------- activation
    async def _activate(self) -> None:
        esc_armed = self._arm_escape()
        if not _screen_indicator_enabled():
            log.debug("[cu-indicator] disabled via [computer_use].screen_indicator")
            return
        ok, reason = self._border_capability()
        if not ok:
            if not self._sidecar_warned:
                self._sidecar_warned = True
                log.info("[cu-indicator] screen border unavailable: %s", reason)
            return
        await asyncio.to_thread(self._spawn_sidecar)
        hint = _ESC_HINTS.get(_resolve_hint_language(), _ESC_HINTS["en"]) if esc_armed else ""
        self._send(protocol.CMD_SHOW, hint=hint)
        if not capture_exclusion_available() and self._proc is not None:
            capture_guard.register_hook(self._suppress_for_grab)

    async def _deactivate(self) -> None:
        self._disarm_escape()
        capture_guard.unregister_hook()
        if self._proc is None:
            return
        self._send(protocol.CMD_HIDE)
        self._send(protocol.CMD_QUIT)
        proc, self._proc = self._proc, None
        await asyncio.to_thread(self._reap, proc)

    @staticmethod
    def _border_capability() -> tuple[bool, str]:
        try:
            from jarvis.platform.probes import (  # noqa: PLC0415
                display_present,
                is_wayland,
            )

            if not display_present():
                return False, "no display on this host (headless)"
            if is_wayland():
                return False, (
                    "Wayland session (no always-on-top overlay surface)"
                )
        except Exception:  # noqa: BLE001
            return False, "platform probes unavailable"
        if importlib.util.find_spec("PySide6") is None:
            return False, (
                "PySide6 not installed (install the [desktop] extra)"
            )
        return True, ""

    # ---------------------------------------------------------------- sidecar
    def _spawn_sidecar(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        try:
            from jarvis.core.process_utils import (  # noqa: PLC0415
                NO_WINDOW_CREATIONFLAGS,
            )

            self._proc = subprocess.Popen(
                [sys.executable, "-m", "jarvis.cu.indicator"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=NO_WINDOW_CREATIONFLAGS,
                env=os.environ.copy(),
            )
        except Exception:  # noqa: BLE001
            self._proc = None
            if not self._sidecar_warned:
                self._sidecar_warned = True
                log.warning(
                    "[cu-indicator] sidecar spawn failed — border disabled "
                    "for this session.", exc_info=True,
                )
            return
        threading.Thread(
            target=self._pump_acks,
            args=(self._proc,),
            name="cu-indicator-acks",
            daemon=True,
        ).start()

    def _pump_acks(self, proc: subprocess.Popen[str]) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                ack = protocol.decode_ack(line)
                if ack is not None:
                    self._acks.put(ack)
        except Exception:  # noqa: BLE001
            log.debug("[cu-indicator] ack pipe closed", exc_info=True)

    def _send(self, cmd: str, **fields: Any) -> bool:
        """Fire-and-forget command write; a dead pipe disables the sidecar."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return False
        try:
            with self._stdin_lock:
                proc.stdin.write(protocol.encode_command(cmd, **fields))
                proc.stdin.flush()
            return True
        except Exception:  # noqa: BLE001
            log.info(
                "[cu-indicator] sidecar pipe broke — border disabled for the "
                "rest of this mission."
            )
            self._proc = None
            capture_guard.unregister_hook()
            return False

    def _reap(self, proc: subprocess.Popen[str]) -> None:
        try:
            proc.wait(timeout=_QUIT_GRACE_S)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                log.debug("[cu-indicator] sidecar reap failed", exc_info=True)

    # ----------------------------------------------------------- capture guard
    @contextmanager
    def _suppress_for_grab(self):
        """Blank the border around one CU frame grab (non-Windows only).

        Fail-open: a late/lost ack only costs the timeout, never the grab.
        """
        while True:  # drop stale acks so we wait on OUR blank
            try:
                self._acks.get_nowait()
            except queue.Empty:
                break
        sent = self._send(protocol.CMD_BLANK)
        if sent:
            try:
                self._acks.get(timeout=_BLANK_ACK_TIMEOUT_S)
            except queue.Empty:
                pass  # fail-open: grab anyway
        try:
            yield
        finally:
            if sent:
                self._send(protocol.CMD_UNBLANK)

    # ---------------------------------------------------------------- escape
    def _arm_escape(self) -> bool:
        try:
            from jarvis.platform.probes import has_hotkey  # noqa: PLC0415

            if not has_hotkey():
                log.info(
                    "[cu-indicator] no global-hotkey backend on this host — "
                    "Escape-to-cancel unavailable."
                )
                return False
        except Exception:  # noqa: BLE001
            return False
        if self._esc_task is None or self._esc_task.done():
            self._esc_task = asyncio.get_running_loop().create_task(
                self._esc_watch(), name="cu-indicator-esc",
            )
        return True

    def _disarm_escape(self) -> None:
        task, self._esc_task = self._esc_task, None
        if task is not None and not task.done():
            task.cancel()

    async def _esc_watch(self) -> None:
        """Armed only while ≥1 CU mission runs; Esc cancels them all."""
        try:
            from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

            # Bare "esc" is intentionally NOT run through validate_hotkey():
            # that guard protects user-configured push-to-talk combos; this
            # binding exists only while Jarvis itself is typing/clicking.
            trigger = HotkeyTrigger({"cu_cancel": ["esc"]})
            async with trigger:
                async for event_name in trigger.events():
                    if event_name != "cu_cancel":
                        continue
                    if self_input.esc_recently_synthesized():
                        log.debug(
                            "[cu-indicator] ignoring Esc synthesized by the "
                            "CU engine itself."
                        )
                        continue
                    self._cancel_all_missions()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning(
                "[cu-indicator] Escape listener failed — cancel via voice/"
                "tray/kill-hotkey still works.", exc_info=True,
            )

    @staticmethod
    def _cancel_all_missions() -> None:
        from jarvis.harness.computer_use_context import (  # noqa: PLC0415
            cancel_active_cu,
        )

        cancelled = cancel_active_cu("user_escape", suppress_new=False)
        log.info(
            "[cu-indicator] Escape pressed — %s.",
            "cancelled the running Computer-Use mission(s)"
            if cancelled
            else "no active Computer-Use mission to cancel",
        )


_controller: CUIndicatorController | None = None


def wire_cu_indicator(bus: Any) -> CUIndicatorController | None:
    """Idempotent boot hook: subscribe the indicator controller to ``bus``.

    Cheap by contract (AP-26): no Qt import, no process spawn, no probe —
    everything heavy waits for the first CUControlStarted.
    """
    global _controller
    if bus is None:
        return None
    if _controller is not None and _controller._bus is bus:
        return _controller
    try:
        controller = CUIndicatorController(bus)
        controller.wire()
    except Exception:  # noqa: BLE001 — the indicator must never break boot
        log.warning("[cu-indicator] wiring failed", exc_info=True)
        return None
    _controller = controller
    return controller


__all__ = ["CUIndicatorController", "wire_cu_indicator"]
