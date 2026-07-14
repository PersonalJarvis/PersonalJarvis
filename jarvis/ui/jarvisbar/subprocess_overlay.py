"""Out-of-process Jarvis Bar surface — the macOS hosting fix (BUG-057).

``SubprocessBarOverlay`` implements the duck-typed surface API
``OrbBusBridge`` drives, but renders nothing itself: it spawns
``python -m jarvis.ui.jarvisbar.host`` — whose MAIN thread may legally own
Aqua-Tk — and forwards every surface call as one JSON line on the child's
stdin. Child events (mute toggle, feedback, show-window) stream back on
stdout and are dispatched to the callbacks the bridge registered, so the
bridge wiring is reused unchanged.

Failure contract: if the host cannot spawn or dies, every method degrades to
a one-log no-op (``NullOverlay`` behavior) — the bar is cosmetic and must
never take the app down with it. No auto-restart in this first cut; a dead
host stays down until the next overlay swap / app restart. Deliberately
defines no ``_root`` attribute so the bridge's reset path early-returns
(same contract as ``NullOverlay``).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import IO, Any

log = logging.getLogger("jarvis.ui.jarvisbar")

# Kept in sync with renderer.MODES without importing numpy/PIL into the
# parent for a pure IPC proxy; the host-side bar re-validates every mode.
_MODES = ("idle", "listen", "speak", "think")

_HOST_MODULE = "jarvis.ui.jarvisbar.host"


class SubprocessBarOverlay:
    """Surface proxy driving a bar hosted in its own companion process."""

    def __init__(
        self,
        persistent: bool = True,
        accent: str = "#e7c46e",
        opacity: float | None = None,
        startup_gated: bool = False,
    ) -> None:
        self._persistent_flag = bool(persistent)
        self._accent = accent
        self._opacity = opacity
        self._startup_gated = bool(startup_gated)
        self._mode = "idle"
        self._muted = False
        self._proc: subprocess.Popen[str] | None = None
        self._send_lock = threading.Lock()
        self._ready = threading.Event()
        self._stopping = False
        self._dead_logged = False
        self._on_mute_toggle: Callable[[], None] | None = None
        self._feedback_publisher: Callable[[str, dict], None] | None = None
        self._on_show_window: Callable[[], None] | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #
    def start_in_thread(self, timeout: float = 3.0) -> None:
        """Spawn the bar host process (name kept for the surface contract)."""
        if self._proc is not None and self._proc.poll() is None:
            return
        try:
            from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

            self._proc = subprocess.Popen(  # noqa: S603 — fixed argv, own venv
                [sys.executable, "-m", _HOST_MODULE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except Exception:  # noqa: BLE001 — cosmetic surface; degrade, never raise
            log.exception("JarvisBar host spawn failed — bar runs as a no-op")
            self._proc = None
            return

        init: dict[str, Any] = {
            "op": "init",
            "persistent": self._persistent_flag,
            "accent": self._accent,
            "startup_gated": self._startup_gated,
        }
        if self._opacity is not None:
            init["opacity"] = float(self._opacity)
        self._write_line(init)

        threading.Thread(
            target=self._pump_events,
            args=(self._proc.stdout,),
            name="jarvisbar-host-events",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pump_stderr,
            args=(self._proc.stderr,),
            name="jarvisbar-host-stderr",
            daemon=True,
        ).start()

        if not self._ready.wait(timeout=timeout):
            log.error("JarvisBar host not ready within %.1fs", timeout)

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._stopping = True
        try:
            self._send({"op": "stop"})
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            log.debug("JarvisBar host stop write failed", exc_info=True)
        try:
            proc.wait(timeout=3.0)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                log.debug("JarvisBar host kill failed", exc_info=True)
        self._proc = None

    # ------------------------------------------------------------------ #
    # Surface API consumed by OrbBusBridge                               #
    # ------------------------------------------------------------------ #
    def show(self, mode: str = "listen") -> None:
        if mode not in _MODES:
            return
        self._mode = mode
        self._send({"op": "show", "mode": mode})

    def hide(self) -> None:
        self._send({"op": "hide"})

    def reassert_z_order(self) -> None:
        self._send({"op": "reassert_z_order"})

    def release_startup_gate(self) -> bool:
        released = self._startup_gated
        self._startup_gated = False
        if released:
            self._send({"op": "release_startup_gate"})
        return released

    def set_level(self, level: float) -> None:
        self._send({"op": "set_level", "level": float(level)})

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._send({"op": "set_muted", "muted": self._muted})

    # The bar draws no text bubble and no mouth — the real surface no-ops
    # these, so the proxy saves the IPC round-trip and no-ops locally too.
    def play_animation(self, name: str, **params: Any) -> None: ...
    def stop_animation(self, name: str) -> None: ...
    def show_listening_transcript(
        self, text: str = "", duration_ms: int = 30000
    ) -> None: ...
    def hide_comment(self) -> None: ...
    def start_mouth_animation(self, duration_ms: int = 60000) -> None: ...
    def stop_mouth_animation(self) -> None: ...

    def set_on_mute_toggle(self, callback: Callable[[], None] | None) -> None:
        self._on_mute_toggle = callback

    def set_feedback_publisher(
        self, callback: Callable[[str, dict], None] | None
    ) -> None:
        self._feedback_publisher = callback

    def set_on_show_window(self, callback: Callable[[], None] | None) -> None:
        self._on_show_window = callback

    def _on_reset_double_click(self, _event: Any = None) -> None:
        self._send({"op": "reset_position"})

    # ``set_bar_persistent`` live-flips ``bar._persistent`` directly; keep
    # that contract while forwarding the flip to the host process.
    @property
    def _persistent(self) -> bool:
        return self._persistent_flag

    @_persistent.setter
    def _persistent(self, enabled: bool) -> None:
        self._persistent_flag = bool(enabled)
        self._send({"op": "set_persistent", "enabled": self._persistent_flag})

    # ------------------------------------------------------------------ #
    # IPC plumbing                                                       #
    # ------------------------------------------------------------------ #
    def _send(self, msg: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            self._log_dead_once()
            return
        self._write_line(msg)

    def _write_line(self, msg: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            line = json.dumps(msg, ensure_ascii=False)
            with self._send_lock:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
        except Exception:  # noqa: BLE001 — broken pipe = dead host; degrade
            self._log_dead_once()

    def _log_dead_once(self) -> None:
        if self._stopping or self._dead_logged:
            return
        self._dead_logged = True
        log.warning(
            "JarvisBar host process is gone — the bar stays hidden until the "
            "next overlay swap or app restart."
        )

    def _pump_events(self, stream: IO[str] | None) -> None:
        if stream is None:
            return
        try:
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.debug("bar host non-JSON stdout line: %.120r", line)
                    continue
                self._dispatch_event(msg)
        except Exception:  # noqa: BLE001
            log.debug("bar host event pump failed", exc_info=True)
        finally:
            if not self._stopping:
                self._log_dead_once()

    def _dispatch_event(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        try:
            if event == "ready":
                self._ready.set()
            elif event == "mute_toggle":
                cb = self._on_mute_toggle
                if cb is not None:
                    cb()
            elif event == "feedback":
                pub = self._feedback_publisher
                if pub is not None:
                    pub(str(msg.get("kind", "")), dict(msg.get("payload") or {}))
            elif event == "show_window":
                cb_show = self._on_show_window
                if cb_show is not None:
                    cb_show()
        except Exception:  # noqa: BLE001 — a bad callback must not kill the pump
            log.exception("bar host event callback failed: %r", event)

    def _pump_stderr(self, stream: IO[str] | None) -> None:
        if stream is None:
            return
        try:
            for raw in stream:
                text = raw.rstrip()
                if text:
                    log.info("bar host: %s", text)
        except Exception:  # noqa: BLE001
            log.debug("bar host stderr pump failed", exc_info=True)
