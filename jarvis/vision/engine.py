"""VisionEngine — orchestrates screenshot and UIA-tree sources.

The engine is the single access point for the CU loop and tools. For each
observe call it heuristically picks whichever source is cheaper/more robust:

- `mode='screenshot'`: image only.
- `mode='ui_tree'`: tree only (pruned).
- `mode='composite'`: both, merged into one observation with `source='full'`.
- `mode='auto'` (default): heuristic choice. Known text-heavy apps
  (Chrome, VSCode, Slack — detected via process name or window title)
  get `screenshot`, because pruning there is too expensive or unstable.
  Everything else gets `composite`.

The engine caches via `VisionCache` on the screenshot hash: if the screen
hasn't changed, it returns the last observation.

Emits `ObservationCaptured` to the optional EventBus — the flight recorder
and the UI subscribe to it.
"""
from __future__ import annotations

import logging
import os
from dataclasses import replace
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

# Process names + window-title fragments for which pruning is too expensive.
# We match case-insensitively against the title OR the process name.
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
    """Orchestrator in front of the sources. See the module docstring."""

    name: str = "vision-engine"
    kind: Literal["screenshot", "ui_tree", "composite"] = "composite"

    def __init__(
        self,
        *,
        screenshot_source: ScreenshotSource | None = None,
        uia_source: UIATreeSource | None = None,
        cache: VisionCache | None = None,
        bus: EventBus | None = None,
        monitor_strategy: Literal["foreground", "primary", "all"] = "foreground",
    ) -> None:
        # ``monitor_strategy`` selects which screen the screenshot source captures.
        # Default "foreground" preserves behaviour for non-CU callers; the factory
        # builds the Computer-Use engine with "primary" so CU stays on the main
        # monitor (multi-monitor-safe). Ignored when an explicit source is injected.
        self._screenshot_source = screenshot_source or ScreenshotSource(
            monitor_strategy=monitor_strategy
        )
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
    ) -> Observation | None:
        """A single observation snapshot.

        `mode='auto'` decides heuristically: if the current foreground
        process is in `_TEXT_HEAVY_HINTS`, use `screenshot`, otherwise
        `composite`.

        The CancelToken is passed down to the sub-sources. Each
        sub-operation additionally checks it at the start.

        Returns None when the screenshot source signals a transient BitBlt /
        GDI failure (ScreenshotSource.observe() returned None). The caller
        (VisionContextProvider._refresh_loop) must treat None as "skip this
        frame" — no cache update, no event emission.
        """
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        hint = self._guess_active_app_hint(window_title_filter)
        effective_mode = self._resolve_mode(mode, hint)

        obs = await self._dispatch(
            effective_mode,
            cancel_token=cancel_token,
            window_title_filter=window_title_filter,
        )

        # Transient BitBlt skip: propagate None to the caller without touching
        # the cache or emitting an event.
        if obs is None:
            return None

        # BUG-CU-EMPTYTITLE (2026-06-09): in screenshot mode the source cannot
        # know the window title, so it stays "". Downstream consumers (the CU
        # loop's regression detector, the cache freshness check) need a real
        # title — and we already probed the foreground window for the mode
        # heuristic, so carry that hint into the observation.
        if effective_mode == "screenshot" and not obs.window_title and hint:
            obs = replace(obs, window_title=hint)

        # Cache check via hash. If we already had the exact same observation,
        # recycle it.
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

    # ---- Heuristics ---------------------------------------------------------

    def _resolve_mode(
        self,
        mode: ObserveMode,
        hint: str,
    ) -> Literal["screenshot", "ui_tree", "composite"]:
        """Turns `auto` into a concrete mode. ``hint`` is the foreground
        hint (title/filter) the caller has already determined."""
        if mode != "auto":
            return mode
        if hint and any(h in hint.lower() for h in _TEXT_HEAVY_HINTS):
            return "screenshot"
        return "composite"

    @staticmethod
    def _guess_active_app_hint(window_title_filter: str | None) -> str:
        """Best-effort active-window hint.

        - If a `window_title_filter` is passed, we use it as the hint — the
          caller typically knows which window it is currently referring to.
        - Otherwise via GetForegroundWindow + GetWindowText (Windows only).
        - On non-Windows we return an empty string, which makes the
          heuristic choose `composite` — pragmatic for tests.
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
        """Checks whether the cached entry still fits the current call.
        We require the same window_title — if the user has switched to a
        different window, the old tree is stale even if the screenshot
        hash happened to be the same.
        """
        return cached.window_title == current.window_title

    # ---- Dispatch ----------------------------------------------------------

    async def _dispatch(
        self,
        mode: Literal["screenshot", "ui_tree", "composite"],
        *,
        cancel_token: CancelToken | None,
        window_title_filter: str | None,
    ) -> Observation | None:
        if mode == "screenshot":
            # Returns None on transient BitBlt failure — propagate to observe().
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
    ) -> Observation | None:
        """Captures both and merges them.

        Returns None when the screenshot source signals a transient BitBlt
        failure — the composite result is unusable without a screenshot.
        """
        shot = await self._screenshot_source.observe(
            cancel_token=cancel_token,
            window_title_filter=window_title_filter,
        )
        # Transient BitBlt skip in composite mode — skip the whole frame.
        if shot is None:
            return None

        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")
        tree = await self._uia_source.observe(
            cancel_token=cancel_token,
            window_title_filter=window_title_filter,
        )

        # If the UIA pruning overflowed, it stays at screenshot_only — the
        # tree source has marked `source='screenshot_only'`. We take the
        # screenshot part and empty nodes.
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

    # ---- Event emission ----------------------------------------------------

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
            logger.warning("ObservationCaptured could not be published",
                           exc_info=True)
