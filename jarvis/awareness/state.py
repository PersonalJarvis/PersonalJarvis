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


# Frames older than this are no longer presented as the *current* foreground
# window — instead the snapshot marks them as a stale "last observed" reading.
# Aligned with the 5-minute idle threshold (AwarenessWatchersConfig
# .idle_threshold_minutes / IdleDetector default 300 s): once a window has not
# changed for that long, asserting it is "currently active" is no longer
# honest. Root cause of the 2026-06-17 "BridgeSpace und WhatsApp" overclaim was
# an ~82-min-old current_frame rendered verbatim as the live state.
_FRAME_MAX_AGE_S: float = 300.0


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

    def snapshot_for_prompt(
        self, max_chars: int = 600, max_age_s: float = _FRAME_MAX_AGE_S,
    ) -> str:
        """Compact plain-text snapshot for system-prompt injection.

        A1: renders ``current_frame`` (title, process, PID) plus optional
        idle status. A2 appends ``last_episode_summary``. A4 appends
        the working set when more than 1 context is active (multiple
        drawers → user needs the comparison). Never raw JSON.

        Freshness guard (2026-06-17): the foreground frame is only presented
        as the *current* window when it is younger than ``max_age_s``.
        An older frame is rendered as a clearly-flagged "last observed"
        reading instead of being narrated as live — otherwise the brain
        reports a stale window state as "currently active" (the root cause
        of the "BridgeSpace und WhatsApp" overclaim). A one-line scope note
        is always appended so the brain never presents this focus history as
        a complete list of every open window/tab.

        When ``current_frame is None``: returns an empty string. Truncated
        to ``max_chars`` with a ``…`` suffix.
        """
        if self.current_frame is None:
            return ""

        import time as _time  # local — no module-level import needed

        cur = self.current_frame
        age_s = max(0, (_time.time_ns() - cur.timestamp_ns) // 1_000_000_000)
        descriptor = (
            f"{cur.active_window_title} "
            f"({cur.active_process_name}, pid={cur.active_pid})"
        )

        parts: list[str]
        if age_s <= max_age_s:
            parts = [f"Currently focused window: {descriptor}"]
        else:
            age_min = age_s // 60
            parts = [
                f"Last observed foreground window ~{age_min} min ago "
                f"(possibly stale — no live screen view right now): {descriptor}"
            ]

        # Scope honesty: awareness only tracks the focused window + recently
        # used apps, never an enumeration of all open windows. Stating this
        # stops the brain from claiming "only X and Y are open on your PC".
        parts.append(
            "(Focused-window history only — NOT a complete list of "
            "open windows/tabs.)"
        )

        if self.is_idle and cur.idle_since_ns is not None:
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
