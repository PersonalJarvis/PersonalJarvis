from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

from jarvis.core.events import SystemStateChanged, TranscriptionUpdate
from ui.orb.bus_bridge import OrbBusBridge
from ui.orb.overlay import (
    BUBBLE_PADDING_Y,
    TRANSCRIPT_MAX_VISIBLE_LINES,
    _transcript_body_height,
    _transcript_visible_line_count,
)


class _FakeBus:
    def subscribe(self, *_args, **_kwargs) -> None:
        pass


class _FakeOrb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def show(self, mode: str = "listen") -> None:
        self.calls.append(("show", mode))

    def play_animation(self, name: str) -> None:
        self.calls.append(("play_animation", name))

    def stop_animation(self, name: str) -> None:
        self.calls.append(("stop_animation", name))

    def show_listening_transcript(
        self, text: str = "", duration_ms: int = 30000
    ) -> None:
        self.calls.append(("show_listening_transcript", text))


async def test_listening_state_opens_large_transcript_bubble_immediately() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )

    assert ("show", "listen") in orb.calls
    assert ("show_listening_transcript", "") in orb.calls


async def test_transcription_update_refreshes_large_bubble_only_while_listening() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Hallo, ich bin cool", is_final=False)
    )

    assert ("show_listening_transcript", "Hallo, ich bin cool") in orb.calls

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Soll nicht sichtbar sein", is_final=False)  # i18n-allow
    )

    assert ("show_listening_transcript", "Soll nicht sichtbar sein") not in orb.calls  # i18n-allow


async def test_final_transcript_during_thinking_still_repaints_the_user_bubble() -> None:
    """Realtime providers deliver (chunks of) the FINAL user transcript
    concurrently with the flip to THINKING. Dropping those froze the bubble on
    the first fragment ("Was" instead of the whole question — live incidents
    2026-07-15/16). While no reply text exists yet, the authoritative final
    user text is never stale and must repaint."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Was", is_final=True)
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(
            text="Was ist morgen für ein Tag?",  # i18n-allow: live-incident transcript under test
            is_final=True,
        )
    )

    assert (
        "show_listening_transcript",
        "Was ist morgen für ein Tag?",  # i18n-allow: live-incident transcript under test
    ) in orb.calls

    # A PARTIAL during THINKING keeps being ignored (stale user-side noise) …
    orb.calls.clear()
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="stale partial", is_final=False)
    )
    assert orb.calls == []

    # … and once the reply text exists, even a FINAL must not repaint.
    bridge._last_response_text = "Tomorrow is Thursday."  # noqa: SLF001
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="late final", is_final=True)
    )
    assert orb.calls == []


def test_transcript_bubble_height_grows_by_visible_line_count() -> None:
    line_height = 20

    assert (
        _transcript_body_height(1, line_height)
        == (BUBBLE_PADDING_Y * 2) + line_height
    )
    assert (
        _transcript_body_height(2, line_height)
        == (BUBBLE_PADDING_Y * 2) + (line_height * 2)
    )
    assert (
        _transcript_body_height(4, line_height)
        == (BUBBLE_PADDING_Y * 2) + (line_height * 4)
    )


def test_transcript_bubble_visible_lines_are_capped_at_four() -> None:
    assert _transcript_visible_line_count(text_height=20, line_height=20) == 1
    assert _transcript_visible_line_count(text_height=70, line_height=20) == 4
    assert (
        _transcript_visible_line_count(text_height=140, line_height=20)
        == TRANSCRIPT_MAX_VISIBLE_LINES
    )
