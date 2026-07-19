"""Out-of-process Jarvis Bar surface — the macOS hosting fix (BUG-057).

``SubprocessBarOverlay`` implements the duck-typed surface API
``OrbBusBridge`` drives, but renders nothing itself: it spawns
``python -m jarvis.ui.jarvisbar.host`` — whose MAIN thread may legally own
Aqua-Tk — and forwards every surface call as one JSON line on the child's
stdin. Child events (mute toggle, feedback, show-window) stream back on
stdout and are dispatched to the callbacks the bridge registered, so the
bridge wiring is reused unchanged.

Failure contract: while the host is down, every method degrades to a one-log
no-op (``NullOverlay`` behavior) — the bar is cosmetic and must never take
the app down with it. Revised 2026-07-18: a live Mac test hit a host death
mid-session and the bar stayed hidden for the rest of it, so a dead host now
gets a BOUNDED auto-respawn — up to ``_RESPAWN_MAX_ATTEMPTS`` attempts per
``SubprocessBarOverlay`` instance lifetime, each preceded by a
``_RESPAWN_BACKOFF_SECONDS`` non-blocking backoff (scheduled on its own
daemon thread, never the caller's) — after which the last known
visibility/mode/mute/level state is re-applied so the bar comes back looking
right instead of blank. Once every attempt is spent the bar reverts to the
original contract: it stays hidden until the next overlay swap or app
restart. Deliberately defines no ``_root`` attribute so the bridge's reset
path early-returns (same contract as ``NullOverlay``).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import IO, Any

log = logging.getLogger("jarvis.ui.jarvisbar")

# Kept in sync with renderer.MODES without importing numpy/PIL into the
# parent for a pure IPC proxy; the host-side bar re-validates every mode.
_MODES = ("idle", "listen", "speak", "think")

_HOST_MODULE = "jarvis.ui.jarvisbar.host"


class SubprocessBarOverlay:
    """Surface proxy driving a bar hosted in its own companion process."""

    # Overridable per surface so a host's pump threads are identifiable.
    _EVENTS_THREAD_NAME = "jarvisbar-host-events"
    _STDERR_THREAD_NAME = "jarvisbar-host-stderr"
    _RESPAWN_THREAD_NAME = "jarvisbar-host-respawn"

    # Bounded auto-respawn (revised 2026-07-18, see module docstring): total
    # attempts per instance lifetime and the non-blocking backoff between
    # them. A death that follows a respawn within seconds still consumes an
    # attempt — the bound itself is what keeps a crash loop from spinning.
    _RESPAWN_MAX_ATTEMPTS = 3
    _RESPAWN_BACKOFF_SECONDS = 5.0

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
        self._visible = False
        self._last_level: float | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._send_lock = threading.Lock()
        self._ready = threading.Event()
        self._stopping = False
        self._dead_logged = False
        self._respawn_lock = threading.Lock()
        self._respawn_attempts = 0
        self._respawn_succeeded = threading.Event()
        self._respawn_exhausted = threading.Event()
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
        self._spawn_process(timeout)

    def _spawn_process(self, timeout: float) -> bool:
        """Popen the host, wire its pump threads, and wait for its ready line.

        Shared by the initial ``start_in_thread`` call and every bounded
        respawn attempt. Returns whether the Popen call itself succeeded —
        a ready-wait timeout is logged but still counts as a live process
        (matches the pre-respawn behavior of ``start_in_thread``).
        """
        self._ready.clear()
        # Reset the death debounce BEFORE the new process (and its pump
        # thread) exist, not after: resetting it only once this method
        # returns would race the new pump thread's own EOF detection —
        # a host that dies again in the instant after spawning could find
        # ``_dead_logged`` still True from the PREVIOUS death and silently
        # swallow its own.
        self._dead_logged = False
        try:
            from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

            proc = subprocess.Popen(  # noqa: S603 — fixed argv, own venv
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
            return False
        self._proc = proc

        self._write_line(self._init_payload())

        # Use the LOCAL ``proc`` reference for both threads, not ``self._proc``
        # again — a fast enough death can already have a respawn attempt
        # reassigning ``self._proc`` (or ``stop()`` clearing it) by the time
        # the second thread starts, and these two threads belong to THIS
        # specific process regardless of what ``self._proc`` points to next.
        threading.Thread(
            target=self._pump_events,
            args=(proc.stdout,),
            name=self._EVENTS_THREAD_NAME,
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pump_stderr,
            args=(proc.stderr,),
            name=self._STDERR_THREAD_NAME,
            daemon=True,
        ).start()

        if not self._ready.wait(timeout=timeout):
            log.error("JarvisBar host not ready within %.1fs", timeout)
        return True

    def stop(self) -> None:
        # Flip this BEFORE the no-proc early return: a spawn failure or a
        # host death may already have a bounded respawn attempt sleeping on
        # its own thread, and that thread's only guard against firing later
        # is this flag — it must be set even when there is no live process
        # to tear down right now.
        self._stopping = True
        proc = self._proc
        if proc is None:
            return
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

    def _init_payload(self) -> dict[str, Any]:
        """First protocol line sent to the freshly spawned host."""
        init: dict[str, Any] = {
            "op": "init",
            "persistent": self._persistent_flag,
            "accent": self._accent,
            "startup_gated": self._startup_gated,
        }
        if self._opacity is not None:
            init["opacity"] = float(self._opacity)
        return init

    # ------------------------------------------------------------------ #
    # Surface API consumed by OrbBusBridge                               #
    # ------------------------------------------------------------------ #
    def show(self, mode: str = "listen") -> None:
        if mode not in _MODES:
            return
        self._mode = mode
        self._visible = True
        self._send({"op": "show", "mode": mode})

    def hide(self) -> None:
        self._visible = False
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
        self._last_level = float(level)
        self._send({"op": "set_level", "level": self._last_level})

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
        """Single choke point for every "the host is gone" detection site.

        ``_dead_logged`` debounces the three call sites (``_send``,
        ``_write_line``, the ``_pump_events`` EOF) for one death; it is reset
        once a respawn succeeds so the NEXT death is detected fresh. Also the
        entry point for the bounded auto-respawn: schedules the next attempt
        while attempts remain, otherwise logs the same honest give-up message
        the surface always used to log unconditionally. The check-then-act
        sequence (debounce, attempt bound, counter increment, thread start)
        runs under ``_respawn_lock`` — the events pump thread and a caller
        thread doing ``show()``/``set_level()``/etc. can both observe the
        same death at once, and without the lock a lost update could double
        -schedule the same attempt number or race ``_spawn_process`` itself.
        """
        with self._respawn_lock:
            if self._stopping or self._dead_logged:
                return
            self._dead_logged = True
            proc = self._proc
            exit_code = proc.poll() if proc is not None else None

            if self._respawn_attempts >= self._RESPAWN_MAX_ATTEMPTS:
                # Log BEFORE setting the event: a waiter unblocked by the
                # event must never observe this method as "not done yet".
                log.warning(
                    "JarvisBar host process is gone (exit code %s) and all "
                    "%d/%d respawn attempts are spent — the bar stays hidden "
                    "until the next overlay swap or app restart.",
                    exit_code,
                    self._respawn_attempts,
                    self._RESPAWN_MAX_ATTEMPTS,
                )
                self._respawn_exhausted.set()
                return

            self._respawn_attempts += 1
            attempt = self._respawn_attempts
            log.warning(
                "JarvisBar host process is gone (exit code %s) — scheduling "
                "respawn attempt %d/%d in %.0fs.",
                exit_code,
                attempt,
                self._RESPAWN_MAX_ATTEMPTS,
                self._RESPAWN_BACKOFF_SECONDS,
            )
            threading.Thread(
                target=self._respawn_after_backoff,
                args=(attempt,),
                name=f"{self._RESPAWN_THREAD_NAME}-{attempt}",
                daemon=True,
            ).start()

    def _respawn_after_backoff(self, attempt: int) -> None:
        """Wait out the backoff, then respawn — runs on its own daemon thread.

        Never touches the caller's thread or the app's event loop: the sleep
        and the Popen call both happen here. A death within the backoff
        window (``stop()`` called while waiting) aborts the attempt instead
        of spawning a host nobody wants anymore.
        """
        time.sleep(self._RESPAWN_BACKOFF_SECONDS)
        if self._stopping:
            return

        if not self._spawn_process(timeout=3.0):
            # The Popen call itself failed; treat it as another death so the
            # same bounded logic decides whether to try again or give up.
            # ``_spawn_process`` already reset ``_dead_logged`` up front.
            self._log_dead_once()
            return

        if self._stopping:
            # stop() raced with this respawn — tear the fresh host back down
            # instead of leaving an orphaned process behind.
            self.stop()
            return

        log.warning(
            "JarvisBar host respawned successfully (attempt %d/%d) — "
            "re-applying the last known bar state.",
            attempt,
            self._RESPAWN_MAX_ATTEMPTS,
        )
        self._respawn_succeeded.set()
        self._reapply_desired_state()

    def _reapply_desired_state(self) -> None:
        """Restore visibility/mode/mute/level onto a freshly respawned host.

        ``_init_payload()`` (re-sent inside ``_spawn_process``) already
        carries persistent/accent/opacity/startup_gated straight from the
        current instance attributes, so only the state this class mirrors
        OUTSIDE the init line — shown/hidden, mode, mute, last level — needs
        a dedicated re-send here.
        """
        if self._visible:
            self._send({"op": "show", "mode": self._mode})
        else:
            self._send({"op": "hide"})
        if self._muted:
            self._send({"op": "set_muted", "muted": True})
        if self._last_level is not None:
            self._send({"op": "set_level", "level": self._last_level})

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


class SubprocessMascotOverlay(SubprocessBarOverlay):
    """Surface proxy driving the mascot ``OrbOverlay`` in the same host.

    Same spawn / ready / EOF-degrade plumbing as the bar proxy — the host
    process picks the surface from the init line's ``"surface"`` key. Unlike
    the bar (which draws no text bubble and no mouth), the mascot renders
    all of them, so the text/mouth/animation ops are FORWARDED over stdio
    instead of no-opped locally.
    """

    _EVENTS_THREAD_NAME = "orb-host-events"
    _STDERR_THREAD_NAME = "orb-host-stderr"
    _RESPAWN_THREAD_NAME = "orb-host-respawn"

    def __init__(self, mascot_path: str | None = None) -> None:
        super().__init__()
        self._mascot_path = mascot_path

    def _init_payload(self) -> dict[str, Any]:
        return {
            "op": "init",
            "surface": "mascot",
            "mascot_path": self._mascot_path,
        }

    # The mascot draws the comment bubble and the mouth — forward the ops the
    # bar proxy no-ops locally (wire shapes match host.dispatch()).
    def play_animation(self, name: str, **params: Any) -> None:
        self._send({"op": "play_animation", "name": str(name), "params": params})

    def stop_animation(self, name: str) -> None:
        self._send({"op": "stop_animation", "name": str(name)})

    def show_listening_transcript(
        self, text: str = "", duration_ms: int = 30000
    ) -> None:
        self._send(
            {
                "op": "show_listening_transcript",
                "text": str(text),
                "duration_ms": int(duration_ms),
            }
        )

    def hide_comment(self) -> None:
        self._send({"op": "hide_comment"})

    def start_mouth_animation(self, duration_ms: int = 60000) -> None:
        self._send({"op": "start_mouth_animation", "duration_ms": int(duration_ms)})

    def stop_mouth_animation(self) -> None:
        self._send({"op": "stop_mouth_animation"})
