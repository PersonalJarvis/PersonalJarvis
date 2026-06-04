"""Rule-based salience scorer for L2 story tracking.

Assigns a score 0-100 to frames (``FrameSnapshot``) and bus events.
Frames with score < ``SALIENCE_THRESHOLD`` are discarded by the
``StoryTracker`` before they enter the Verdichter input — token budget
protection.

The scorer is intentionally **rule-based, not an LLM**: deterministic,
no additional brain round-trip on the voice critical path.

Plan reference: §6 "Salience Scorer (verbindlich, rule-based)".
"""
from __future__ import annotations

from typing import Any

from jarvis.awareness.state import FrameSnapshot

# Frames below this score are NOT included in episode condensation.
SALIENCE_THRESHOLD: int = 30

# Lower-case for case-insensitive matching against ``process_name.lower()``.
# Tray helpers / shell UI are noise — the user is not "working" here.
BORING_PROCESSES: frozenset[str] = frozenset({
    "explorer.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
    "ctfmon.exe",
    "dwm.exe",
})

# Threshold for "long dwell in the same frame" (>2min ⇒ +10).
_LONG_DWELL_NS: int = 120_000_000_000

# Default score when no prev frame exists (first frame of the session).
# Neutral mid-score so the first real workspace frame does not immediately
# fall below SALIENCE_THRESHOLD — the BORING penalty still applies.
_BASE_SCORE_NO_PREV: int = 50


class SalienceScorer:
    """Rule-based score 0-100 for frames and events.

    No LLM, no I/O — pure function. Called exactly once per frame by the
    ``StoryTracker``.
    """

    def score_frame(
        self, frame: FrameSnapshot, *, prev: FrameSnapshot | None,
    ) -> int:
        """Score 0-100 for a single frame.

        Components (additive, then clamped to [0, 100]):

        - +20 if ``process_name`` changed relative to ``prev`` (app switch)
        - +30 if ``window_title`` changed but process stayed the same
          (file/tab switch within the same app)
        - +20 if ``git_branch`` (non-None in both frames) differs
        - +10 if dwell time ``frame.timestamp_ns - prev.timestamp_ns``
          > 2 min (user worked on the same frame for a long time)
        - -50 if ``process_name.lower()`` is in ``BORING_PROCESSES``

        With ``prev=None`` (first frame): base score 50 + BORING penalty.
        Never raises NULL/Exception — the caller relies on that guarantee.
        """
        process_name = frame.active_process_name
        boring_penalty = -50 if process_name.lower() in BORING_PROCESSES else 0

        if prev is None:
            return self._clamp(_BASE_SCORE_NO_PREV + boring_penalty)

        score = 0

        # App switch: strongest signal for an episode trigger.
        process_changed = process_name != prev.active_process_name
        if process_changed:
            score += 20

        # Title switch within the same app (only when NO process switch,
        # otherwise we would double-count — an app switch implies a title change).
        elif frame.active_window_title != prev.active_window_title:
            score += 30

        # Git branch change (only when both are set — None does not compare).
        if (
            frame.git_branch is not None
            and prev.git_branch is not None
            and frame.git_branch != prev.git_branch
        ):
            score += 20

        # Long dwell time (>2 min) → user worked focused on the same frame.
        if (frame.timestamp_ns - prev.timestamp_ns) > _LONG_DWELL_NS:
            score += 10

        score += boring_penalty
        return self._clamp(score)

    def score_event(
        self, event_kind: str, payload: dict[str, Any] | None = None,
    ) -> int:
        """Score 0-100 for bus events (FileSaved, BrainTurnCompleted, …).

        Mapping (default 0 for unknown ``event_kind``):

        - ``"FileSaved"`` → 40
        - ``"TerminalExit"`` → 60 if ``payload["exit_code"] != 0`` (error!),
          otherwise 20 (clean exit is less interesting)
        - ``"BrainTurnCompleted"`` → 50 (response from main Jarvis = story marker)
        - ``"IdleExited"`` → 20 (attention returns)
        """
        if event_kind == "FileSaved":
            return 40
        if event_kind == "TerminalExit":
            exit_code = (payload or {}).get("exit_code", 0)
            return 60 if exit_code != 0 else 20
        if event_kind == "BrainTurnCompleted":
            return 50
        if event_kind == "IdleExited":
            return 20
        return 0

    @staticmethod
    def _clamp(score: int) -> int:
        """Clamp score to [0, 100] — hard negative §6."""
        if score < 0:
            return 0
        if score > 100:
            return 100
        return score
