"""Recover a running task's clock without treating app uptime as work time.

This is timing evidence only. The activity watcher still decides whether the
agent is working. Transcript IO belongs on a worker thread during adoption,
never in the activity sweep or a badge's request handler.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path


def timestamp(value: Any) -> float:
    """A finite positive epoch timestamp, or unknown for legacy/broken data."""
    if isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        logger.debug("Agentic IDE: ignoring an invalid saved task timestamp")
        return 0.0
    return result if math.isfinite(result) and result > 0 else 0.0


def active_start(events: list[dict[str, Any]]) -> float:
    """Start of the last unfinished task, including time awaiting first output."""
    started = 0.0
    for event in events:
        kind = event.get("kind")
        at = timestamp(event.get("ts_ms")) / 1000.0
        if kind == "user_message":
            started = at
        elif kind == "turn_started" and not started:
            started = at
        elif kind == "turn_finished":
            started = 0.0
    return started


def recover_start(agent: str, session_id: str, home: Path | None) -> float | None:
    """Read the CLI's existing record; None means no timing source is available."""
    from .agent_transcript import read_events

    events = read_events(agent, session_id, home=home, live=True)
    return None if events is None else active_start(events)
