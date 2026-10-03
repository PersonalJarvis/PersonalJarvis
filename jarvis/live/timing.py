"""Content-free, once-per-call startup milestones on separate monotonic clocks."""

from __future__ import annotations

import logging
import math
import time

log = logging.getLogger(__name__)

BROWSER_PHASES = frozenset({
    "capture_ready", "first_capture_frame", "offer_ready", "socket_open",
    "answer_received", "media_connected", "input_released", "first_output_audio",
})


class StartupTimings:
    """Measure readiness separately from first input/output; never log audio or SDP."""

    def __init__(self, session_id: str, started_at: float | None = None) -> None:
        self.session_id = session_id
        self.started_at = time.monotonic() if started_at is None else started_at
        self.marks: dict[str, float] = {}
        self.browser_marks: dict[str, float] = {}

    def mark(self, phase: str) -> None:
        if phase in self.marks:
            return
        elapsed = max(0.0, (time.monotonic() - self.started_at) * 1000)
        self.marks[phase] = elapsed
        log.info("Voice startup session=%s clock=backend phase=%s elapsed_ms=%.1f",
                 self.session_id, phase, elapsed)

    def browser(self, marks: object) -> None:
        # Client telemetry is untrusted. Only a bounded numeric allowlist can
        # reach logs; client offsets are NEVER subtracted from server clocks.
        if not isinstance(marks, dict) or len(marks) > len(BROWSER_PHASES):
            return
        for phase, elapsed in marks.items():
            if (phase not in BROWSER_PHASES or phase in self.browser_marks
                    or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                    or not math.isfinite(elapsed) or not 0 <= elapsed <= 300_000):
                continue
            self.browser_marks[phase] = elapsed
            log.info("Voice startup session=%s clock=browser phase=%s elapsed_ms=%.1f",
                     self.session_id, phase, elapsed)
