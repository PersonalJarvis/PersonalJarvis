"""VisionEngine — orchestriert Screenshot- und UIA-Tree-Sources.

Die Engine ist der einzige Zugriffspunkt fuer CU-Loop und Tools. Sie waehlt
pro Observe-Call heuristisch, welche Source billiger/robuster ist:

- `mode='screenshot'`: nur Bild.
- `mode='ui_tree'`: nur Baum (gepruneded).
- `mode='composite'`: beides, gemergt zu einer Observation mit `source='full'`.
- `mode='auto'` (Default): heuristische Wahl. Bekannte Text-heavy Apps
  (Chrome, VSCode, Slack — erkannt an Process-Name oder Window-Title)
  bekommen `screenshot`, weil Pruning dort zu teuer oder instabil ist.
  Alles andere bekommt `composite`.

Die Engine cached ueber `VisionCache` auf dem Screenshot-Hash: wenn der
Bildschirm sich nicht veraendert hat, gibt sie die letzte Observation
zurueck.

Emittiert `ObservationCaptured` an den optionalen EventBus — der Flight-
Recorder und die UI subscriben darauf.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Literal

from jarvis.core.events import ObservationCaptured
from jarvis.core.protocols import CancelToken, Observation, UIANode

from .cache import VisionCache
from .screenshot import ScreenshotSource
from .tree_factory import make_ui_tree_source
from .uia_tree import UIATreeSource

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

logger = logging.getLogger(__name__)

ObserveMode = Literal["auto", "screenshot", "ui_tree", "composite"]

# Process-Namen + Window-Title-Fragmente, fuer die Pruning zu teuer ist.
# Wir matchen case-insensitive gegen Title ODER Process-Name.
_TEXT_HEAVY_HINTS: tuple[str, ...] = (
    "chrome",
    "chromium",
    "msedge",
    "firefox",
    "code",          # VSCode
    "visual studio code",
    "slack",
    "discord",
    "teams",
)


class VisionEngine:
    """Orchestrator vor den Sources. Siehe Modul-Docstring."""

    name: str = "vision-engine"
    kind: Literal["screenshot", "ui_tree", "composite"] = "composite"

    def __init__(
        self,
        *,
        screenshot_source: ScreenshotSource | None = None,
        uia_source: UIATreeSource | None = None,
        cache: VisionCache | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._screenshot_source = screenshot_source or ScreenshotSource()
        # AD-10: per-OS UI-tree source (UIA on Windows, AX on macOS, AT-SPI on
        # Linux, null elsewhere) selected by the factory; the explicit
        # ``uia_source`` DI argument still overrides it for tests.
        self._uia_source = uia_source or make_ui_tree_source()
        self._cache = cache or VisionCache()
        self._bus = bus
        self._last_active_window: str = ""

    # ---- Public API --------------------------------------------------------

    async def observe(
        self,
        *,
        mode: ObserveMode = "auto",
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        """Einzelner Observation-Snapshot.

        `mode='auto'` entscheidet heuristisch: wenn der aktuelle
        Foreground-Process in `_TEXT_HEAVY_HINTS` liegt, nimm `screenshot`,
        sonst `composite`.

        CancelToken wird an die Sub-Sources weitergereicht. Jeder
        Sub-Operation-Start prueft zusaetzlich selbst.
        """
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        effective_mode = self._resolve_mode(mode, window_title_filter)

        obs = await self._dispatch(
            effective_mode,
            cancel_token=cancel_token,
            window_title_filter=window_title_filter,
        )

        # Cache-Check ueber Hash. Wenn wir die exakt gleiche Observation
        # schon hatten, recyclen wir.
        cached = self._cache.get(obs.screenshot_hash)
        if cached is not None and self._cache_is_fresh(cached, obs):
            await self._emit(cached)
            return cached
        self._cache.put(obs)
        await self._emit(obs)
        return obs

    async def close(self) -> None:
        await self._screenshot_source.close()
        await self._uia_source.close()
        self._cache.clear()

    # ---- Heuristik ---------------------------------------------------------

    def _resolve_mode(
        self,
        mode: ObserveMode,
        window_title_filter: str | None,
    ) -> Literal["screenshot", "ui_tree", "composite"]:
        """Wandelt `auto` in einen konkreten Modus um."""
        if mode != "auto":
            return mode
        hint = self._guess_active_app_hint(window_title_filter)
        if hint and any(h in hint.lower() for h in _TEXT_HEAVY_HINTS):
            return "screenshot"
        return "composite"

    @staticmethod
    def _guess_active_app_hint(window_title_filter: str | None) -> str:
        """Best-effort Active-Window-Hinweis.

        - Wenn ein `window_title_filter` mitgegeben ist, nutzen wir ihn als
          Hint — der Caller weiss typischerweise, auf welches Fenster er
          sich gerade bezieht.
        - Sonst via GetForegroundWindow + GetWindowText (nur Windows).
        - Auf non-Windows liefern wir einen leeren String, wodurch die
          Heuristik `composite` waehlt — pragmatisch fuer Tests.
        """
        if window_title_filter:
            return window_title_filter
        if os.name != "nt":
            return ""
        try:
            import ctypes  # noqa: PLC0415

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return ""
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or ""
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _cache_is_fresh(cached: Observation, current: Observation) -> bool:
        """Prueft, ob der gecachte Eintrag noch fuer den aktuellen Call
        passt. Wir verlangen denselben window_title — wenn der User in ein
        anderes Fenster gewechselt hat, ist der alte Tree stale, selbst
        wenn der Screenshot-Hash (zufaellig) der gleiche waere.
        """
        return cached.window_title == current.window_title

    # ---- Dispatch ----------------------------------------------------------

    async def _dispatch(
        self,
        mode: Literal["screenshot", "ui_tree", "composite"],
        *,
        cancel_token: CancelToken | None,
        window_title_filter: str | None,
    ) -> Observation:
        if mode == "screenshot":
            return await self._screenshot_source.observe(
                cancel_token=cancel_token,
                window_title_filter=window_title_filter,
            )
        if mode == "ui_tree":
            return await self._uia_source.observe(
                cancel_token=cancel_token,
                window_title_filter=window_title_filter,
            )
        # composite
        return await self._compose(
            cancel_token=cancel_token,
            window_title_filter=window_title_filter,
        )

    async def _compose(
        self,
        *,
        cancel_token: CancelToken | None,
        window_title_filter: str | None,
    ) -> Observation:
        """Beides aufnehmen und mergen."""
        shot = await self._screenshot_source.observe(
            cancel_token=cancel_token,
            window_title_filter=window_title_filter,
        )
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")
        tree = await self._uia_source.observe(
            cancel_token=cancel_token,
            window_title_filter=window_title_filter,
        )

        # Wenn das UIA-Pruning overflow-te, bleibt's bei screenshot_only —
        # die Tree-Source hat `source='screenshot_only'` markiert. Wir
        # uebernehmen den Screenshot-Part und leere Nodes.
        if tree.source == "screenshot_only":
            merged_nodes: tuple[UIANode, ...] = ()
            merged_source: Literal["full", "screenshot_only", "ui_tree_only"] = (
                "screenshot_only"
            )
        else:
            merged_nodes = tree.nodes
            merged_source = "full"

        return Observation(
            trace_id=shot.trace_id,
            timestamp_ns=shot.timestamp_ns,
            screenshot_path=shot.screenshot_path,
            screenshot_hash=shot.screenshot_hash,
            nodes=merged_nodes,
            window_title=tree.window_title,
            active_pid=tree.active_pid,
            source=merged_source,
            pruning_stats=tree.pruning_stats,
        )

    # ---- Event-Emission ----------------------------------------------------

    async def _emit(self, obs: Observation) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                ObservationCaptured(
                    trace_id=obs.trace_id,
                    timestamp_ns=obs.timestamp_ns,
                    source=obs.source,
                    window_title=obs.window_title,
                    node_count=len(obs.nodes),
                    screenshot_hash=obs.screenshot_hash,
                    screenshot_path=obs.screenshot_path,
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("ObservationCaptured konnte nicht publiziert werden",
                           exc_info=True)
