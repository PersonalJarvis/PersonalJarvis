"""Browser startup milestones must survive the backend's telemetry allowlist."""

import re
from pathlib import Path

from jarvis.live.timing import BROWSER_PHASES, StartupTimings


def test_frontend_startup_milestones_are_accepted_without_clock_conversion():
    source = (
        Path(__file__).resolve().parents[3]
        / "jarvis/ui/web/frontend/src/lib/realtimeAudio.ts"
    ).read_text(encoding="utf-8")
    phases = set(re.findall(
        r'\b(?:markStartup|markLocalStartup|onStartupPhase)(?:\?\.)?\(\s*"([^"]+)"',
        source,
    ))
    assert phases
    assert phases <= BROWSER_PHASES
    timing = StartupTimings("browser-startup-test")
    expected = {phase: float(index * 10) for index, phase in enumerate(sorted(phases))}
    # Match the browser's bounded compatibility batches.
    pairs = list(expected.items())
    for offset in range(0, len(pairs), 8):
        timing.browser(dict(pairs[offset:offset + 8]))
    assert timing.browser_marks == expected
