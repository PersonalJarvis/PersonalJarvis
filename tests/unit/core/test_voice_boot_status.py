"""Strict voice usability must not be confused with a released loading UI."""
from __future__ import annotations

import pytest

from jarvis.core.events import VoiceBootStatus


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (VoiceBootStatus(ready=False, detail="warmup_start"), False),
        (VoiceBootStatus(ready=True, detail="listening"), True),
        (VoiceBootStatus(ready=True), True),
        (VoiceBootStatus(ready=True, detail="voice_unavailable"), False),
        (VoiceBootStatus(ready=True, detail="watchdog_timeout"), False),
    ],
)
def test_voice_usable_excludes_degraded_ui_release_events(
    event: VoiceBootStatus, expected: bool
) -> None:
    assert event.voice_usable is expected
