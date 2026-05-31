"""VisionContextProvider - Background-Cache frischer Screen-Observations.

Hybrid-Refresh-Strategie: Ein async Background-Task refresht alle
`refresh_interval_s` ueber `VisionEngine.observe(mode=capture_mode)` und
haelt die letzte Observation im Cache. `current()` liefert sie sofort -
oder forced einen Fresh-Capture wenn der Cache aelter als
`max_staleness_s` ist (Liveness-Garantie).

Lifecycle:
    provider = VisionContextProvider(engine, bus=bus)
    await provider.start()       # Task laeuft
    obs = await provider.current()
    provider.pause()             # Privacy-Toggle
    provider.resume()
    await provider.stop()        # Task canceled, < 500ms

Pausiert `current()` wirft `VisionPaused`. Exceptions im Loop werden
geloggt, Loop laeuft weiter (stirbt nur via stop()).

Design-Notiz (Reality-Check: Provider.start() sync-startbar): `start()`
ist zwar `async def`, macht aber nur `asyncio.create_task(...)` - das
laeuft in jedem Event-Loop-Kontext und blockiert nicht. Wer den Provider
aus sync-Code startet, kann `asyncio.run(provider.start())` nutzen oder
den Task spaeter in seinem eigenen Loop erzeugen.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Literal

from jarvis.core.protocols import Observation

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.vision.engine import VisionEngine

log = logging.getLogger(__name__)

CaptureMode = Literal["auto", "screenshot", "ui_tree", "composite"]


class VisionPaused(RuntimeError):
    """Raised by VisionContextProvider.current() wenn pausiert."""


class VisionContextProvider:
    """Background-Cache-Layer vor einer VisionEngine.

    Haelt die juengste Observation im Speicher und refresht sie periodisch
    im Hintergrund. Konsumenten bekommen so Screen-Kontext ohne die
    Latenz eines frischen Captures - es sei denn der Cache ist aelter
    als `max_staleness_s`, dann wird synchron nachgezogen.
    """

    def __init__(
        self,
        engine: VisionEngine,
        *,
        bus: EventBus | None = None,
        refresh_interval_s: float = 2.0,
        max_staleness_s: float = 2.0,
        capture_mode: CaptureMode = "screenshot",
    ) -> None:
        self._engine = engine
        self._bus = bus
        self._refresh_interval_s = float(refresh_interval_s)
        self._max_staleness_s = float(max_staleness_s)
        self._capture_mode: CaptureMode = capture_mode
        self._latest: Observation | None = None
        self._paused: bool = False
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False

    # ---------- Lifecycle ----------

    async def start(self) -> None:
        """Startet den Background-Refresh-Loop auf dem aktuellen Event-Loop.

        Idempotent: Mehrfacher Aufruf ohne vorheriges stop() ist ein No-op.
        """
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._refresh_loop(),
            name="vision-context-refresh",
        )

    async def stop(self) -> None:
        """Cancelt den Loop und wartet max 500ms auf sauberes Beenden.

        Nach stop() ist der Provider wieder startbar via start().
        """
        self._stopping = True
        t = self._task
        if t is None:
            return
        if not t.done():
            t.cancel()
            try:
                await asyncio.wait_for(t, timeout=0.5)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("VisionContextProvider stop() swallow: %s", exc)
        self._task = None

    # ---------- Public Access ----------

    async def current(self, *, force_refresh: bool = False) -> Observation:
        """Liefert aktuellste Observation.

        Forced einen frischen Capture wenn (a) explizit via `force_refresh`,
        (b) noch keine Observation vorliegt oder (c) die vorhandene aelter
        als `max_staleness_s` ist.

        Raises:
            VisionPaused: wenn der Provider gerade pausiert ist.
        """
        if self._paused:
            raise VisionPaused("Vision pausiert")
        need_fresh = (
            force_refresh
            or self._latest is None
            or self._age_s(self._latest) > self._max_staleness_s
        )
        if need_fresh:
            obs = await self._engine.observe(mode=self._capture_mode)
            self._latest = obs
            return obs
        return self._latest

    def pause(self) -> None:
        """Privacy-Toggle: Loop refresht nicht, current() wirft VisionPaused."""
        self._paused = True

    def resume(self) -> None:
        """Hebt pause() auf. Loop refresht beim naechsten Tick wieder."""
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def latest(self) -> Observation | None:
        """Letzte gecachte Observation ohne Refresh-Check (kann stale sein)."""
        return self._latest

    # ---------- Internals ----------

    @staticmethod
    def _age_s(obs: Observation) -> float:
        """Alter der Observation in Sekunden, geclampt auf >= 0."""
        return max(0.0, time.time_ns() / 1e9 - obs.timestamp_ns / 1e9)

    async def _refresh_loop(self) -> None:
        """Background-Task: periodisch observe() aufrufen und cachen.

        Exceptions werden geloggt aber nicht propagiert - der Loop stirbt
        ausschliesslich ueber stop() (-> CancelledError).

        Error-Logging: die ERSTE Exception (oder erste nach 5 aufeinander
        folgenden Fehlern) wird als `error` mit Stacktrace geloggt, damit sie
        im Flight-Recorder + UI sichtbar ist. Weitere gleichartige Fehler
        werden nur als `warning` ohne Trace reported, damit die Logs nicht
        fluten (typischer Fall: mss scheitert wegen RDP-Lock-Screen und
        wiederholt sich alle 2s bis zum Unlock).
        """
        consecutive_errors = 0
        while not self._stopping:
            try:
                if not self._paused:
                    obs = await self._engine.observe(mode=self._capture_mode)
                    self._latest = obs
                    if consecutive_errors > 0:
                        log.info(
                            "VisionContextProvider recovered nach %d Fehlern.",
                            consecutive_errors,
                        )
                    consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                # Erster Fehler sowie jeder 5. Folgefehler: laut mit Trace.
                if consecutive_errors == 1 or consecutive_errors % 5 == 0:
                    log.error(
                        "VisionContextProvider Loop-Exception (#%d): %s "
                        "(retry in %.2fs)",
                        consecutive_errors,
                        exc,
                        self._refresh_interval_s,
                        exc_info=True,
                    )
                else:
                    log.warning(
                        "VisionContextProvider Loop-Exception (#%d): %s",
                        consecutive_errors,
                        exc,
                    )
            try:
                await asyncio.sleep(self._refresh_interval_s)
            except asyncio.CancelledError:
                raise
