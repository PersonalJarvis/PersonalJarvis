"""Process-wide access to the errand runner.

One runner per process, built lazily from whatever the running app has already
wired up. Errands are started from a brain tool, from the CLI and (later) from
the UI, and all three must reach the SAME runner — two runners over one
database would each resume the other's work after a restart.

Prototype seam, stated honestly: the pieces are read off ``BrainManager`` by
attribute, because it currently exposes no accessor for its tool map, executor
or brain. That is a wiring shortcut, not a design; the follow-up is a small
public accessor on the manager, at which point only this file changes. Every
lookup degrades to "not available" rather than raising, so a partially wired
app reports honestly instead of crashing a turn.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jarvis.core import runtime_refs

from .brain_legs import BrainLegExecutor
from .bridge import ErrandEventBridge
from .runner import ErrandRunner
from .store import ErrandStore

log = logging.getLogger(__name__)

_RUNNER: list[ErrandRunner] = []
_EVENT_BUS: list[Any] = []


def set_event_bus(bus: Any) -> None:
    """Register the global EventBus so errands can report back.

    Called once at server startup, BEFORE the first errand starts. Without it
    the runner still works — but every state change lands only in SQLite,
    which is exactly the silent-outcome hole this seam exists to close.
    """
    _EVENT_BUS.clear()
    _EVENT_BUS.append(bus)


def _data_dir(config: Any) -> Path:
    """Where the shared SQLite database lives.

    Mirrors the task/workflow convention: errands are an additive schema on
    ``data/jarvis.db`` rather than a database of their own.
    """
    memory_cfg = getattr(config, "memory", None)
    raw = getattr(memory_cfg, "data_dir", None) or "data"
    return Path(raw)


def _resolve_brain(manager: Any, config: Any) -> Any | None:
    """The brain an errand thinks with."""
    brain_cfg = getattr(config, "brain", None)
    provider = getattr(brain_cfg, "provider", None)
    getter = getattr(manager, "_get_brain", None)
    if not provider or not callable(getter):
        return None
    try:
        return getter(provider)
    except Exception:  # noqa: BLE001 — a missing provider is a report, not a crash
        log.warning("errands: could not resolve a brain provider", exc_info=True)
        return None


def get_runner() -> ErrandRunner | None:
    """The shared runner, or None when the app is not wired for errands yet."""
    if _RUNNER:
        return _RUNNER[0]

    manager = runtime_refs.get_brain_manager()
    if manager is None:
        return None

    config = getattr(manager, "_config", None)
    tools = dict(getattr(manager, "_tools", {}) or {})
    executor = getattr(manager, "_tool_executor", None)
    brain = _resolve_brain(manager, config)
    if config is None or executor is None or brain is None or not tools:
        log.info("errands: brain stack not fully wired — runner unavailable")
        return None

    # An errand must never dispatch another errand: the runner already owns the
    # long-running loop, and a nested one would run a second unbounded loop
    # inside a leg. Same reasoning as AP-5/AP-14 for spawn tools in worker sets.
    tools.pop("start_errand", None)

    if not _EVENT_BUS:
        log.info("errands: no event bus registered — outcomes will not be announced")
    runner = ErrandRunner(
        store=ErrandStore(_data_dir(config) / "jarvis.db"),
        execute_leg=BrainLegExecutor(brain=brain, tools=tools, executor=executor),
        on_update=ErrandEventBridge(_EVENT_BUS[0]) if _EVENT_BUS else None,
    )
    _RUNNER.append(runner)
    return runner


def reset_runner() -> None:
    """Drop the cached runner and bus. Tests only."""
    _RUNNER.clear()
    _EVENT_BUS.clear()


__all__ = ["get_runner", "reset_runner", "set_event_bus"]
