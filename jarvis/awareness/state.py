"""Data models for the L1 live-frame layer.

``FrameSnapshot`` is immutable (frozen+slots), created by the
``WindowFocusWatcher`` in phase A1 and written into
``AwarenessState.current_frame`` in the drain loop. Immutability lets
the flight recorder deterministically replay every frame.

``AwarenessState`` is mutable (classic dataclass without ``frozen``)
because the ``AwarenessManager`` holds it as live state and tools such
as ``awareness-snapshot`` read from it synchronously — without a
brain/IO round-trip.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.awareness.working_set import WorkingSet


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    """A single L1 frame.

    Mandatory fields are set by the ``WindowFocusWatcher`` (A1).
    Optional fields are populated only by phase-A5 probes
    (GitProbe, IDE-LSP/MCP). ``idle_since_ns`` carries the timestamp
    of the last input when the ``IdleDetector`` actively reports "idle".
    """
    timestamp_ns: int
    active_window_title: str
    active_process_name: str
    active_pid: int
    is_capture_allowed: bool
    git_branch: str | None = None
    open_file_hint: str | None = None
    idle_since_ns: int | None = None


@dataclass
class AwarenessState:
    """Mutable live state. Held by the ``AwarenessManager``."""
    current_frame: FrameSnapshot | None = None
    last_episode_summary: str = ""           # populated in A2 (StoryTracker)
    last_episode_id: int | None = None       # populated in A2
    # A4: WorkingSet reference, set by AwarenessManager. ``None`` in
    # A0/A1 use without manager wiring. ``snapshot_for_prompt`` renders
    # the working set when it has > 1 slot (otherwise the "last episode"
    # line is sufficient).
    working_set: WorkingSet | None = field(default=None)
    is_idle: bool = False

    def snapshot_for_prompt(self, max_chars: int = 600) -> str:
        """Compact plain-text snapshot for system-prompt injection.

        A1: renders ``current_frame`` (title, process, PID) plus optional
        idle status. A2 appends ``last_episode_summary``. A4 appends
        the working set when more than 1 context is active (multiple
        drawers → user needs the comparison). Never raw JSON.

        When ``current_frame is None``: returns an empty string. Truncated
        to ``max_chars`` with a ``…`` suffix.
        """
        if self.current_frame is None:
            return ""

        cur = self.current_frame
        parts: list[str] = [
            f"Aktiv: {cur.active_window_title} "
            f"({cur.active_process_name}, pid={cur.active_pid})",
        ]

        if self.is_idle and cur.idle_since_ns is not None:
            import time as _time  # local — no module-level import needed

            idle_seconds = max(0, (_time.time_ns() - cur.idle_since_ns) // 1_000_000_000)
            idle_minutes = idle_seconds // 60
            parts.append(f"[Idle seit {idle_minutes}min]")

        # A4: multi-context render when > 1 slot is active. With only 1 slot,
        # the working-set render is redundant next to the "last episode" line.
        if self.working_set is not None:
            ws_text = self.working_set.render_for_prompt()
            if ws_text:
                parts.append(ws_text)

        if self.last_episode_summary:    # populated from A2 onwards
            parts.append(f"Letzte Episode: {self.last_episode_summary}")

        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text
