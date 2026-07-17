"""Standalone Jarvis Bar host process — main-thread Tk hosting (BUG-057 fix).

Aqua-Tk (like AppKit) is main-thread-only on macOS: a Tk root created on any
worker thread aborts the WHOLE process with a native, uncatchable assertion
(BUG-057, same class as the BUG-056 tray). The desktop app cannot host the
bar in-process there — its main thread belongs to pywebview/Cocoa. This
module is the documented fix ("hosted in its own process"): a minimal
companion process whose MAIN thread runs the bar's Tk mainloop, remote-driven
by the parent app over a line-oriented JSON protocol.

Protocol (UTF-8, one JSON object per line):

- parent → child (stdin): the first line is the init object
  ``{"op": "init", "persistent": ..., "accent": ..., "startup_gated": ...}``
  (optional ``opacity``); every further line is a surface command mirroring
  the ``OrbBusBridge`` surface API (``show``, ``hide``, ``set_level``, ...).
  An optional ``"surface"`` key selects what the host renders:
  ``"jarvis_bar"`` (default) or ``"mascot"`` (the OrbOverlay mascot, with an
  optional ``"mascot_path"`` passthrough).
  stdin EOF means the parent died or shut down → the host stops the bar and
  exits, so no ownerless bar can linger on the user's desktop.
- child → parent (stdout): events — ``{"event": "ready"}`` once the Tk root
  is initialized, plus user interactions (``mute_toggle``, ``feedback``,
  ``show_window``). Logging goes to stderr so stdout stays pure protocol.

The host works on every OS (the parent simply only uses it where in-process
hosting is impossible). ``JARVIS_BAR_HOST_FAKE=1`` swaps the Tk bar for an
echo double so the full cross-process pipeline is testable without a display.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from typing import Any, TextIO

log = logging.getLogger("jarvis.ui.jarvisbar.host")

READY_TIMEOUT_S = 30.0
# After stdin EOF the Tk mainloop should unwind via bar.stop(); if Tk wedges,
# this backstop hard-exits so the orphaned host can never outlive its parent.
HARD_EXIT_GRACE_S = 5.0


def emit(event: str, **payload: Any) -> None:
    """Write one child→parent event line to stdout (thread-safe)."""
    line = json.dumps({"event": event, **payload}, ensure_ascii=False)
    with _STDOUT_LOCK:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 — parent gone; reader loop sees EOF too
            log.debug("bar-host event write failed", exc_info=True)


_STDOUT_LOCK = threading.Lock()


def _call(surface: Any, name: str, *args: Any, **kwargs: Any) -> None:
    """Invoke ``surface.<name>(...)`` if present; a missing method is a no-op.

    The host now fronts more than one surface class (JarvisBarOverlay has
    every op; the mascot OrbOverlay lacks a few bar-only ones), so an op the
    current surface does not implement degrades to a debug-logged no-op.
    """
    method = getattr(surface, name, None)
    if method is None:
        log.debug("bar-host: surface has no %r — op ignored", name)
        return
    method(*args, **kwargs)


def dispatch(surface: Any, msg: dict[str, Any]) -> bool:
    """Apply one parent command to the surface. Returns ``False`` for ``stop``.

    Every method called here is documented thread-safe on the surface
    (enqueue onto the Tk UI queue or an atomic write), so the stdin reader
    thread may call them directly.
    """
    op = msg.get("op")
    if op == "stop":
        return False
    if op == "show":
        _call(surface, "show", str(msg.get("mode", "listen")))
    elif op == "hide":
        _call(surface, "hide")
    elif op == "set_level":
        _call(surface, "set_level", float(msg.get("level", 0.0)))
    elif op == "set_muted":
        _call(surface, "set_muted", bool(msg.get("muted", False)))
    elif op == "set_persistent":
        # Live flag flip — the same plain attribute write the in-process
        # set_bar_persistent path performs (no Tk marshal needed).
        surface._persistent = bool(msg.get("enabled", True))  # noqa: SLF001
    elif op == "release_startup_gate":
        _call(surface, "release_startup_gate")
    elif op == "reassert_z_order":
        _call(surface, "reassert_z_order")
    elif op == "play_animation":
        _call(
            surface,
            "play_animation",
            str(msg.get("name", "")),
            **dict(msg.get("params") or {}),
        )
    elif op == "stop_animation":
        _call(surface, "stop_animation", str(msg.get("name", "")))
    elif op == "show_listening_transcript":
        _call(
            surface,
            "show_listening_transcript",
            str(msg.get("text", "")),
            int(msg.get("duration_ms", 30000)),
        )
    elif op == "hide_comment":
        _call(surface, "hide_comment")
    elif op == "start_mouth_animation":
        _call(surface, "start_mouth_animation", int(msg.get("duration_ms", 60000)))
    elif op == "stop_mouth_animation":
        _call(surface, "stop_mouth_animation")
    elif op == "reset_position":
        # The double-click reset seam.
        _call(surface, "_on_reset_double_click")
    else:
        log.warning("bar-host: unknown op %r", op)
    return True


def reader_loop(
    surface: Any,
    stream: TextIO,
    *,
    hard_exit: Callable[[int], Any] | None = None,
) -> None:
    """Drain parent commands until EOF or ``stop``, then stop the surface.

    ``hard_exit`` (production: ``os._exit``) is the anti-linger backstop for
    a Tk mainloop that refuses to unwind; injectable so tests never arm it.
    """
    try:
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                log.warning("bar-host: dropping non-JSON line: %.120r", line)
                continue
            try:
                if not dispatch(surface, msg):
                    break
            except Exception:  # noqa: BLE001 — one bad command must not kill the bar
                log.exception("bar-host command failed: %r", msg.get("op"))
    finally:
        # EOF = the parent died or closed us deliberately; either way the
        # surface has no owner anymore. Arm the anti-linger backstop BEFORE
        # stopping the surface: stop() lets the main thread's mainloop return
        # immediately, and starting a thread while the interpreter is already
        # shutting down is a fatal error (0xC0000409 on Windows).
        if hard_exit is not None:
            killer = threading.Timer(HARD_EXIT_GRACE_S, hard_exit, args=(0,))
            killer.daemon = True
            killer.start()
        # stop() marshals destroy onto the Tk thread.
        try:
            surface.stop()
        except Exception:  # noqa: BLE001
            log.debug("bar-host stop failed", exc_info=True)


class _EchoBar:
    """No-Tk protocol double (``JARVIS_BAR_HOST_FAKE=1``).

    Satisfies the lifecycle contract (``_started`` + a blocking ``start()``)
    and echoes every surface call back as an ``op`` event, so tests exercise
    the real pipes, threads, EOF and shutdown paths without a display.
    """

    def __init__(self, **_: Any) -> None:
        self._persistent = True
        self._started = threading.Event()
        self._stop_evt = threading.Event()

    def start(self) -> None:
        self._started.set()
        self._stop_evt.wait()

    def stop(self) -> None:
        self._stop_evt.set()

    def set_on_mute_toggle(self, cb: Any) -> None: ...
    def set_feedback_publisher(self, cb: Any) -> None: ...
    def set_on_show_window(self, cb: Any) -> None: ...

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        def _echo(*args: Any, **kwargs: Any) -> None:
            emit("op", op=name, args=list(args), kwargs=kwargs)

        return _echo


def _hide_dock_icon() -> None:
    """Best-effort: run as a Dock-less accessory app (macOS only).

    Without this the bar host would add a second python rocket to the Dock.
    pyobjc may be absent — purely cosmetic, never blocks the bar.
    """
    try:
        from AppKit import NSApplication  # type: ignore[import-not-found]

        # 1 = NSApplicationActivationPolicyAccessory: windows allowed, no
        # Dock icon, no menu bar takeover — exactly a floating overlay.
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:  # noqa: BLE001
        log.debug("Dock-icon hide skipped (pyobjc unavailable?)", exc_info=True)


def _import_orb_overlay() -> Any:
    """Import the mascot ``OrbOverlay`` — wheel-robust.

    The top-level ``ui`` package ships with the source tree but not the
    wheel; when ``import ui`` fails, retry with the ``jarvis`` package's
    parent directory (the source checkout root) on ``sys.path``. A final
    failure exits the host non-zero with an honest stderr line so the
    parent can degrade.
    """
    try:
        import ui  # noqa: F401
    except ImportError:
        try:
            from pathlib import Path

            import jarvis

            root = str(Path(jarvis.__file__).resolve().parent.parent)
            if root not in sys.path:
                sys.path.insert(0, root)
        except Exception:  # noqa: BLE001 — the import below reports the failure
            log.debug("mascot-host: sys.path fallback failed", exc_info=True)
    try:
        from ui.orb.overlay import OrbOverlay
    except Exception as exc:  # noqa: BLE001
        log.error(
            "mascot-host: cannot import OrbOverlay (%s) — exiting so the "
            "parent can degrade",
            exc,
        )
        raise SystemExit(3) from exc
    return OrbOverlay


def _build_surface(cfg: dict[str, Any]) -> Any:
    if os.environ.get("JARVIS_BAR_HOST_FAKE") == "1":
        return _EchoBar()
    if sys.platform == "darwin":
        _hide_dock_icon()
    if str(cfg.get("surface", "jarvis_bar")) == "mascot":
        orb_overlay_cls = _import_orb_overlay()
        return orb_overlay_cls(
            sticky=False,
            mic_reactive=False,
            style="mascot",
            mascot_path=cfg.get("mascot_path") or None,
        )
    from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay

    kwargs = {
        key: cfg[key]
        for key in ("persistent", "accent", "opacity", "startup_gated")
        if key in cfg
    }
    return JarvisBarOverlay(**kwargs)


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # UTF-8 pipes on every OS (Windows would otherwise default to cp1252).
    for stream in (sys.stdin, sys.stdout):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    init_raw = sys.stdin.readline()
    try:
        cfg = json.loads(init_raw)
        if cfg.get("op") != "init":
            raise ValueError(f"expected init, got {cfg.get('op')!r}")
    except ValueError:
        log.error("bar-host: invalid init line %.200r — exiting", init_raw)
        return 2

    surface = _build_surface(cfg)
    surface.set_on_mute_toggle(lambda: emit("mute_toggle"))
    surface.set_feedback_publisher(
        lambda kind, payload: emit("feedback", kind=kind, payload=payload)
    )
    surface.set_on_show_window(lambda: emit("show_window"))

    def _announce_ready() -> None:
        if surface._started.wait(timeout=READY_TIMEOUT_S):  # noqa: SLF001
            emit("ready")
        else:
            log.error(
                "bar-host: surface did not initialize within %ss", READY_TIMEOUT_S
            )

    threading.Thread(
        target=_announce_ready, name="barhost-ready", daemon=True
    ).start()
    threading.Thread(
        target=reader_loop,
        args=(surface, sys.stdin),
        kwargs={"hard_exit": os._exit},
        name="barhost-stdin",
        daemon=True,
    ).start()

    # THE point of this process: the Tk mainloop runs on the MAIN thread, the
    # only thread Aqua-Tk accepts on macOS.
    surface.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
