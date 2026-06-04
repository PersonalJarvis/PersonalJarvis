"""Live-Smoke fuer Phase A1 — Awareness L1 Live Frame.

Initialisiert ``AwarenessManager`` + Watchers (WindowFocusWatcher +
IdleDetector), laeuft N Sekunden (Default 30s), loggt jedes
``FrameUpdated``- und ``IdleEntered/Exited``-Event auf stdout.

Saubereres Shutdown via Ctrl+C: ``signal.signal(SIGINT, ...)`` setzt ein
asyncio.Event, das die main-Coroutine zum Aufwachen bringt; danach
``await manager.stop()`` mit 2s-Timeout (siehe Plan §5 Hard-Negative).

Manuell: 5x das Foreground-Window wechseln (Alt+Tab oder Klick auf
andere App), die Window-Titles/Processes erscheinen in der Ausgabe.

Usage:
    python scripts/awareness_smoke_a1.py [--seconds 30]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import subprocess
import sys

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AwarenessCaptureBlocked,
    FrameUpdated,
    IdleEntered,
    IdleExited,
)


async def _auto_trigger_window_switches(log: logging.Logger, count: int = 5) -> None:
    """Oeffnet/schliesst N-mal notepad.exe — triggert reproduzierbar
    EVENT_SYSTEM_FOREGROUND. Genutzt fuer CI-tauglichen Selbsttest ohne
    User-Interaction.
    """
    for i in range(count):
        await asyncio.sleep(1.0)
        log.info("AUTO-TRIGGER #%d  spawning notepad.exe", i + 1)
        try:
            proc = subprocess.Popen(["notepad.exe"])  # noqa: ASYNC220
        except OSError as exc:
            log.warning("AUTO-TRIGGER #%d  failed to spawn: %s", i + 1, exc)
            continue
        await asyncio.sleep(1.5)
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass


async def run_smoke(seconds: int, auto_trigger: bool, debug: bool) -> int:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("smoke-a1")
    if debug:
        logging.getLogger("jarvis.awareness").setLevel(logging.DEBUG)

    bus = EventBus()
    config = AwarenessConfig.default()
    manager = AwarenessManager(config, bus=bus)

    counter = {"frame": 0, "idle_in": 0, "idle_out": 0, "blocked": 0}

    async def on_frame(ev: FrameUpdated) -> None:
        counter["frame"] += 1
        log.info(
            "FRAME #%d  title=%r  process=%r  pid=%d  capture_allowed=%s",
            counter["frame"], ev.window_title, ev.process_name,
            ev.pid, ev.is_capture_allowed,
        )

    async def on_idle_in(ev: IdleEntered) -> None:
        counter["idle_in"] += 1
        log.info("IDLE-IN   idle_since_ns=%d", ev.idle_since_ns)

    async def on_idle_out(ev: IdleExited) -> None:
        counter["idle_out"] += 1
        log.info("IDLE-OUT  was_idle_for_ms=%d", ev.was_idle_for_ms)

    async def on_blocked(ev: AwarenessCaptureBlocked) -> None:
        counter["blocked"] += 1
        log.info(
            "BLOCKED   title=%r  process=%r  reason=%s",
            ev.window_title, ev.process_name, ev.reason,
        )

    bus.subscribe(FrameUpdated, on_frame)
    bus.subscribe(IdleEntered, on_idle_in)
    bus.subscribe(IdleExited, on_idle_out)
    bus.subscribe(AwarenessCaptureBlocked, on_blocked)

    log.info(
        "Starte AwarenessManager — enable_window=%s enable_idle=%s "
        "idle_threshold=%dmin",
        config.watchers.enable_window, config.watchers.enable_idle,
        config.watchers.idle_threshold_minutes,
    )
    await manager.start()
    log.info(
        "OK — Watchers aktiv (count=%d). %ds Lauf-Zeit. "
        "Bitte Foreground-Window 5x wechseln (Alt+Tab oder Klick). "
        "Ctrl+C beendet sofort.",
        len(manager._watchers), seconds,
    )

    trigger_task: asyncio.Task[None] | None = None
    if auto_trigger:
        log.info("AUTO-TRIGGER aktiv — starte notepad-spawn-loop")
        trigger_task = asyncio.create_task(
            _auto_trigger_window_switches(log, count=5),
            name="auto-trigger",
        )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(_signum: int, _frame: object) -> None:
        log.info("Stop-Signal empfangen — fahre runter")
        loop.call_soon_threadsafe(stop_event.set)

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _on_signal)

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=float(seconds))
        log.info("Shutdown via Signal")
    except TimeoutError:
        log.info("Zeit abgelaufen (%ds) — stoppe Watchers", seconds)

    if trigger_task is not None and not trigger_task.done():
        trigger_task.cancel()
        try:
            await trigger_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            # Smoke-Cleanup — kein logging noetig, das Skript endet ohnehin.
            pass

    # Watcher-Stats VOR stop() sammeln — danach ist die Liste leer.
    watcher_stats = [
        (type(w).__name__, getattr(w, "_drops", None), getattr(w, "_last_hwnd", None))
        for w in (getattr(manager, "_watchers", []) or [])
    ]
    await manager.stop()
    for name, drops, last_hwnd in watcher_stats:
        log.info("WATCHER-STAT %s  drops=%s  last_hwnd=%s", name, drops, last_hwnd)

    log.info(
        "DONE — Frames=%d  Idle-In=%d  Idle-Out=%d  Blocked=%d",
        counter["frame"], counter["idle_in"],
        counter["idle_out"], counter["blocked"],
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Awareness A1 live smoke")
    parser.add_argument(
        "--seconds", type=int, default=30,
        help="Lauf-Dauer in Sekunden (Default 30)",
    )
    parser.add_argument(
        "--auto-trigger", action="store_true",
        help="Auto-Spawn 5x notepad.exe — triggert Hooks ohne User-Action",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="DEBUG-Level Logging fuer jarvis.awareness.*",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print(
            "[SKIP] awareness_smoke_a1.py ist Win32-only "
            "(SetWinEventHook fehlt auf diesem OS)",
            file=sys.stderr,
        )
        return 0

    return asyncio.run(run_smoke(args.seconds, args.auto_trigger, args.debug))


if __name__ == "__main__":
    sys.exit(main())
