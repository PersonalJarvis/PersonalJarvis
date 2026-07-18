"""Vision anticipation for ``spawn_worker`` (persona mandate Phase 5).

Wave-4 migration: previously ``spawn_sub_jarvis``. Provides a short
Active-Window-Hint (``"User active in: <process_name> (<window_title>)"``)
as an additional ``context_hint`` at spawn time. The OpenClaw worker gets
free context about which application the user is currently working in —
without burdening the main Jarvis prompt with a permanent vision block.

Activation (default OFF):
  - ENV ``JARVIS_VISION_CONTEXT=1``  OR
  - ``[vision].context_hint_on_spawn = true`` in jarvis.toml

Mandate latency budget: 250 ms. On timeout or crash no hint is produced —
the spawn continues without one. Failure mode 4 (mandate): pywinauto
crashes on RDP/headless sessions because ``GetForegroundWindow`` returns no
window there. A ``try/except`` with a ``Warning`` log catches this.

Performance probe ``.tmp_research/vision_latency_probe.py`` shows p95
= 1.4 ms on target hardware (RTX 5070 Ti, Win11) — in pywinauto fallback
mode (nodes=0). Production latency with pywinauto installed is typically
50–200 ms for simple apps; the timeout guards against worst-case scenarios
(Chrome with many tabs, slow UIA bridges).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.core.config import VisionContextConfig
    from jarvis.vision.engine import VisionEngine


log = logging.getLogger(__name__)

_ENV_FLAG = "JARVIS_VISION_CONTEXT"


def is_enabled(config: VisionContextConfig | None = None) -> bool:
    """Return True if vision context is enabled (ENV OR config flag)."""
    env = os.environ.get(_ENV_FLAG, "").strip().lower()
    if env in ("1", "true", "on", "yes"):
        return True
    if config is not None and config.context_hint_on_spawn:
        return True
    return False


async def get_active_window_hint(
    *,
    engine: VisionEngine | None = None,
    config: VisionContextConfig | None = None,
) -> str | None:
    """Return a short Active-Window-Hint, or ``None``.

    Returns ``None`` when:
      - Vision context is disabled (ENV/config not set)
      - VisionEngine.observe raises an exception (failure mode 4: pywinauto crash)
      - Timeout (latency cap 250 ms from config or default)
      - Observation has neither ``window_title`` nor ``active_pid``

    Args:
        engine: Optional. If ``None``, a new engine is created
            (cheap — sources are lazily initialised).
        config: Optional. If ``None``, defaults are used
            (``timeout_s=0.25``, no context-hint-on-spawn).
    """
    if not is_enabled(config):
        return None

    if engine is None:
        from jarvis.vision.engine import VisionEngine
        engine = VisionEngine()

    timeout_s = config.timeout_s if config is not None else 0.25

    try:
        obs = await asyncio.wait_for(
            engine.observe(mode="ui_tree"),
            timeout=timeout_s,
        )
    except TimeoutError:
        log.warning(
            "Vision context: timeout after %.0f ms — no hint",
            timeout_s * 1000,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        # Failure mode 4: pywinauto crash (RDP/headless), UIA source issues,
        # permission denied — none of these are fatal spawn blockers.
        log.warning(
            "Vision context: observe() failed (%s: %s) — no hint",
            type(exc).__name__, exc,
        )
        return None

    title = (obs.window_title or "").strip()
    process_name = _process_name_for_pid(obs.active_pid)

    if not title and not process_name:
        return None
    if process_name and title:
        return f"User active in: {process_name} ({title})"
    if process_name:
        return f"User active in: {process_name}"
    return f"User active in: {title}"


def _process_name_for_pid(pid: int | None) -> str:
    """Return the process name for a PID (or ``""`` on error).

    Uses ``psutil`` (best-effort). If psutil is not installed or the PID
    does not exist, no hint is produced — the caller falls back to
    ``window_title``-only.
    """
    if not pid:
        return ""
    try:
        import psutil  # type: ignore[import-not-found]

        return psutil.Process(int(pid)).name() or ""
    except Exception:  # noqa: BLE001
        return ""


__all__ = [
    "is_enabled",
    "get_active_window_hint",
]
