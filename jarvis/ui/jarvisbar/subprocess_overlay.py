"""Out-of-process Jarvis Bar surface — the macOS hosting fix (BUG-057).

``SubprocessBarOverlay`` implements the duck-typed surface API
``OrbBusBridge`` drives, but renders nothing itself: it spawns
``python -m jarvis.ui.jarvisbar.host`` — whose MAIN thread may legally own
Aqua-Tk — and forwards every surface call as one JSON line on the child's
stdin. Child events (talk, hang-up, mute toggle, feedback, show-window, drop)
stream back on stdout. Voice actions are executed against the live
parent-process pipeline; bridge-owned actions are dispatched to its registered
callbacks. A forwarded drop is replayed onto THIS process's drop bridge (the
brain lives here, not in the host) and its verdict is sent back down so the
hosted bar can confirm the drop on screen.

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
import weakref
from collections.abc import Callable
from typing import IO, Any

# The SAME coarse-mode tuple every other layer validates against — imported,
# never copied. ``jarvis.ui.jarvisbar.modes`` has no imports at all, so this
# stays a pure IPC proxy with no numpy/PIL in the parent process. The
# hand-copied predecessor is precisely why ``show("dictate")`` was a silent
# no-op on the macOS surface for weeks (AP-4 / BUG-008).
from jarvis.ui.jarvisbar.modes import MODES as _MODES

log = logging.getLogger("jarvis.ui.jarvisbar")


def _respawn_after_backoff_weakly(
    weak_surface: weakref.ReferenceType[SubprocessBarOverlay],
    attempt: int,
    backoff: float,
) -> None:
    """Serve a respawn backoff without keeping the overlay alive.

    Deliberately a module function taking a weak reference, not a bound
    method: a sleeping thread that holds the surface strongly resurrects an
    overlay nobody wants anymore, and spawns a REAL host process — with a real
    window — once the sleep ends. An overlay that has been dropped has no bar
    to restore, so the correct respawn is none.
    """
    time.sleep(backoff)
    surface = weak_surface()
    if surface is None:
        log.debug(
            "JarvisBar respawn attempt %d abandoned — the overlay it belonged "
            "to was released during the backoff.",
            attempt,
        )
        return
    surface._respawn_after_backoff(attempt)

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
        size_scale: float = 1.0,
        follow_cursor_monitor: bool = True,
    ) -> None:
        self._persistent_flag = bool(persistent)
        self._accent = accent
        self._opacity = opacity
        self._startup_gated = bool(startup_gated)
        # User "Bar size" multiplier, forwarded in the init line so the host
        # boots at the right size and a bounded respawn restores it.
        self._size_scale = float(size_scale)
        # "Follow the mouse to the active monitor" preference, forwarded in the
        # init line so the host boots with it and a bounded respawn restores it.
        self._follow_cursor_monitor = bool(follow_cursor_monitor)
        self._mode = "idle"
        self._muted = False
        # Mirror what the real host does at construction time. A persistent,
        # ungated bar maps itself immediately; a startup-gated or
        # non-persistent bar starts withdrawn. This mirror is load-bearing for
        # respawn: _reapply_desired_state must not hide a bar that the host just
        # restored after a crash/reload.
        self._visible = self._persistent_flag and not self._startup_gated
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
        self._on_talk: Callable[[], None] | None = None
        self._on_hangup: Callable[[], None] | None = None
        self._feedback_publisher: Callable[[str, dict], None] | None = None
        self._on_show_window: Callable[[], None] | None = None
        self._on_speaker_toggle: Callable[[], None] | None = None

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
        # Release the drop return leg: a swap to another surface installs its
        # own sink, and a stale one would write to a host that is going away.
        try:
            from jarvis.overlay.drop_bridge import set_drop_result_sink

            set_drop_result_sink(None)
        except Exception:  # noqa: BLE001 — teardown must never raise
            log.debug("drop result sink release failed", exc_info=True)
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
            "size_scale": self._size_scale,
            "follow_cursor_monitor": self._follow_cursor_monitor,
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
        # Match JarvisBarOverlay.show(): a non-persistent idle bar withdraws
        # instead of becoming visible. Active modes and every persistent mode
        # are visible once the startup gate permits them.
        self._visible = self._persistent_flag or mode != "idle"
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
            # The host maps a persistent bar immediately on gate release. Keep
            # the proxy's desired-state mirror in lock-step so a later bounded
            # respawn replays ``show`` rather than hiding the restored bar.
            self._visible = self._persistent_flag or self._mode != "idle"
            self._send({"op": "release_startup_gate"})
        return released

    def set_level(self, level: float) -> None:
        self._last_level = float(level)
        self._send({"op": "set_level", "level": self._last_level})

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._send({"op": "set_muted", "muted": self._muted})

    def set_size_scale(self, scale: float) -> None:
        """Forward a live "Bar size" change to the hosted surface.

        Stored too, so a bounded respawn re-sends the latest size in the fresh
        host's init line rather than snapping back to the boot value."""
        self._size_scale = float(scale)
        self._send({"op": "set_size_scale", "scale": self._size_scale})

    def set_follow_cursor(self, enabled: bool) -> None:
        """Forward a live 'follow the active monitor' toggle to the hosted bar.

        Stored too, so a bounded respawn re-sends the latest value in the fresh
        host's init line rather than snapping back to the boot value."""
        self._follow_cursor_monitor = bool(enabled)
        self._send({"op": "set_follow_cursor", "enabled": self._follow_cursor_monitor})

    # The bar draws no text bubble and no mouth — the real surface no-ops
    # these, so the proxy saves the IPC round-trip and no-ops locally too.
    def play_animation(self, name: str, **params: Any) -> None: ...
    def stop_animation(self, name: str) -> None: ...
    def show_listening_transcript(self, text: str = "", duration_ms: int = 30000) -> None: ...
    def hide_comment(self) -> None: ...
    def start_mouth_animation(self, duration_ms: int = 60000) -> None: ...
    def stop_mouth_animation(self) -> None: ...

    def set_on_mute_toggle(self, callback: Callable[[], None] | None) -> None:
        self._on_mute_toggle = callback

    def set_on_talk(self, callback: Callable[[], None] | None) -> None:
        self._on_talk = callback

    def set_on_hangup(self, callback: Callable[[], None] | None) -> None:
        self._on_hangup = callback

    def set_feedback_publisher(self, callback: Callable[[str, dict], None] | None) -> None:
        self._feedback_publisher = callback

    def set_on_show_window(self, callback: Callable[[], None] | None) -> None:
        self._on_show_window = callback

    def set_on_speaker_toggle(self, callback: Callable[[], None] | None) -> None:
        self._on_speaker_toggle = callback

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
            # The thread must NOT hold this surface alive across the backoff.
            # A bound method would: the sleeping thread keeps a strong ref, so
            # an overlay nobody wants anymore still reaches Popen minutes later
            # and puts a real bar window on the user's screen. That is exactly
            # how a unit run leaked a second, permanently visible bar — the
            # test's Popen fake was long unpatched by the time the sleep ended.
            # A weakref makes "nobody holds this overlay" mean "no respawn".
            threading.Thread(
                target=_respawn_after_backoff_weakly,
                args=(weakref.ref(self), attempt, self._RESPAWN_BACKOFF_SECONDS),
                name=f"{self._RESPAWN_THREAD_NAME}-{attempt}",
                daemon=True,
            ).start()

    def _respawn_after_backoff(self, attempt: int) -> None:
        """Respawn the host — runs on its own daemon thread, backoff already served.

        Never touches the caller's thread or the app's event loop: the Popen
        call happens here. The backoff itself is served by
        :func:`_respawn_after_backoff_weakly`, which holds only a weak
        reference while it sleeps. A death within that window (``stop()``
        called while waiting) aborts the attempt instead of spawning a host
        nobody wants anymore.
        """
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
            elif event == "talk":
                self._dispatch_talk_action()
            elif event == "hangup":
                self._dispatch_hangup_action()
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
            elif event == "speaker_toggle":
                self._dispatch_speaker_toggle()
            elif event == "drop":
                self._dispatch_drop_event(msg)
        except Exception:  # noqa: BLE001 — a bad callback must not kill the pump
            log.exception("bar host event callback failed: %r", event)

    def _dispatch_drop_event(self, msg: dict[str, Any]) -> None:
        """Re-dispatch a drop the hosted bar received, here in the parent.

        The child process has no brain and no asyncio loop — its bridge only
        forwards. This process has the real handler (installed by the desktop
        app), so the drop is replayed onto the parent-side bridge and the
        verdict is sent back down as a ``drop_result`` command, which the host
        turns into the bar's visible confirmation.
        """
        from jarvis.overlay.drop_bridge import dispatch_drop, set_drop_result_sink

        paths = [str(p) for p in (msg.get("paths") or [])]
        text = str(msg.get("text") or "")
        # Install the return leg lazily and idempotently: it can only be
        # answered while a host is alive, and binding it here keeps it on the
        # instance that actually received the drop.
        set_drop_result_sink(self._send_drop_result)
        if not dispatch_drop(paths, text):
            # No handler in this process either (headless embedder, or a drop
            # that beat the desktop wiring). Say so rather than leaving the bar
            # waiting on a confirmation that will never come.
            log.debug("bar host drop had no parent-side handler")
            self._send_drop_result(False)

    def _send_drop_result(self, accepted: bool) -> None:
        """Send a drop verdict down to the hosted bar (never raises)."""
        self._send({"op": "drop_result", "accepted": bool(accepted)})

    def _dispatch_speaker_toggle(self) -> None:
        """Mute / unmute the assistant's voice, here in the parent.

        The orb's speaker disc is drawn in the child, but the TTS volume it
        changes belongs to the SpeechPipeline, which only ever exists in this
        process. An explicitly installed callback wins for embedders and tests.
        """
        callback = self._on_speaker_toggle
        if callback is not None:
            callback()
            return
        from ui.orb.controls import toggle_speaker_mute

        if toggle_speaker_mute() is None:
            log.debug("orb speaker toggle had no live pipeline in the parent")

    def _dispatch_talk_action(self) -> None:
        """Start a voice session in the parent process.

        An explicitly installed callback wins for embedders/tests. The normal
        macOS desktop path falls through to ``runtime_refs``, which is populated
        in this parent process (and deliberately empty in the Tk host child).
        """
        callback = self._on_talk
        if callback is not None:
            callback()
            return

        from jarvis.core.runtime_refs import get_speech_pipeline

        pipeline = get_speech_pipeline()
        if pipeline is None:
            return
        start = getattr(pipeline, "request_voice_session", None)
        if callable(start):
            start()

    def _dispatch_hangup_action(self) -> None:
        """Apply the close-X active-session guard in the parent process."""
        callback = self._on_hangup
        if callback is not None:
            callback()
            return

        from jarvis.core.runtime_refs import get_speech_pipeline

        pipeline = get_speech_pipeline()
        if pipeline is None:
            return

        # Preserve the in-process bar contract: an active-looking bar with no
        # live session is a stuck visual state, so the close click starts a
        # session and lets the user escape. Pipelines without the probe retain
        # the legacy fail-safe and receive a normal hang-up request.
        active = getattr(pipeline, "is_session_active", None)
        if callable(active) and not active():
            start = getattr(pipeline, "request_voice_session", None)
            if callable(start):
                start()
            return

        hangup = getattr(pipeline, "request_hangup", None)
        if callable(hangup):
            hangup()

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
    """Surface proxy driving the orb window (``OrbOverlay``) in the same host.

    Same spawn / ready / EOF-degrade plumbing as the bar proxy — the host
    process picks the surface from the init line's ``"surface"`` key and the
    look from ``"style"`` (``mascot`` or ``voice_orb``). Unlike the bar (which
    draws no text bubble and no mouth), the orb window renders all of them, so
    the text/mouth/animation ops are FORWARDED over stdio instead of no-opped
    locally.
    """

    _EVENTS_THREAD_NAME = "orb-host-events"
    _STDERR_THREAD_NAME = "orb-host-stderr"
    _RESPAWN_THREAD_NAME = "orb-host-respawn"

    def __init__(self, mascot_path: str | None = None, style: str = "mascot") -> None:
        super().__init__()
        self._mascot_path = mascot_path
        self._style = str(style or "mascot")
        # OrbOverlay(sticky=False) always starts withdrawn. The base proxy's
        # persistent-bar visibility default does not apply to this surface.
        self._visible = False

    def _init_payload(self) -> dict[str, Any]:
        return {
            "op": "init",
            "surface": "mascot",
            "style": self._style,
            "mascot_path": self._mascot_path,
        }

    def set_style(self, style: str) -> None:
        """Re-style the hosted orb window live (mascot <-> voice orb).

        Remembered locally as well: a host respawn re-sends the init line, and
        without this the window would come back wearing the OLD look.
        """
        self._style = str(style or "mascot")
        self._send({"op": "set_style", "style": self._style})

    # The mascot draws the comment bubble and the mouth — forward the ops the
    # bar proxy no-ops locally (wire shapes match host.dispatch()).
    def play_animation(self, name: str, **params: Any) -> None:
        self._send({"op": "play_animation", "name": str(name), "params": params})

    def stop_animation(self, name: str) -> None:
        self._send({"op": "stop_animation", "name": str(name)})

    def show_listening_transcript(self, text: str = "", duration_ms: int = 30000) -> None:
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
