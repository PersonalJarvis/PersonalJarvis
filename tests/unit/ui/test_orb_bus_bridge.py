from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Repo root in sys.path so the top-level module `ui.orb.*` is importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

# `ui` lives as a top-level directory in the repo root (not under `jarvis/`).
# Some pytest setups don't recognize this path via the discovery loader; in that
# case the tests are skipped instead of blowing up test collection.
try:  # noqa: SIM105 — intentional try-import for the discovery quirk
    from ui.orb.bus_bridge import (  # type: ignore[import-not-found]
        THINKING_BUBBLE_TEXT,
        OrbBusBridge,
    )
    from ui.orb.overlay import OrbOverlay  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "ui.orb not available on the pytest PYTHONPATH — top-level namespace package. "
        "Test runs directly with `python -m pytest tests/unit/ui/...` from the repo root, "
        "when the repo root is manually placed on PYTHONPATH.",
        allow_module_level=True,
    )

from jarvis.core.events import (
    AudioOutFirst,
    JarvisAgentBackgroundCompleted,
    ResponseGenerated,
    SystemStateChanged,
    TranscriptionUpdate,
    WakeWordDetected,
    VoiceBootStatus,
    VoiceSessionEnded,
    VoiceSessionStarted,
)


class _FakeBus:
    def subscribe(self, *_args, **_kwargs) -> None:
        pass


class _RecordingBus:
    def __init__(self) -> None:
        self.subscriptions = []

    def subscribe(self, event_type, handler) -> None:
        self.subscriptions.append((event_type, handler))


class _FakeOrb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._mode = "idle"

    def show(self, mode: str = "listen") -> None:
        self._mode = mode
        self.calls.append(("show", mode))

    def set_level(self, level: float) -> None:
        self.calls.append(("set_level", level))

    def hide(self) -> None:
        self.calls.append(("hide", None))

    def set_mode(self, mode: str) -> None:
        self.calls.append(("set_mode", mode))

    def play_animation(self, name: str) -> None:
        self.calls.append(("play_animation", name))

    def stop_animation(self, name: str) -> None:
        self.calls.append(("stop_animation", name))

    def show_listening_transcript(
        self, text: str = "", duration_ms: int = 30000
    ) -> None:
        self.calls.append(("show_listening_transcript", text))

    def hide_comment(self) -> None:
        self.calls.append(("hide_comment", None))

    def show_comment(self, text: str, duration_ms: int = 3500) -> None:
        self.calls.append(("show_comment", text))

    def set_on_mute_toggle(self, callback) -> None:
        self.calls.append(("set_on_mute_toggle", callback))
        self.mute_callback = callback


class _FakeBarWithExplicitReassert(_FakeOrb):
    def reassert_z_order(self) -> None:
        self.calls.append(("reassert_z_order", None))


class _FakeStartupGatedBar(_FakeBarWithExplicitReassert):
    def __init__(self) -> None:
        super().__init__()
        self.startup_gated = True

    def release_startup_gate(self) -> bool:
        self.calls.append(("release_startup_gate", None))
        if not self.startup_gated:
            return False
        self.startup_gated = False
        return True


async def test_orb_is_shown_again_for_thinking_after_external_hide() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )

    assert ("show", "think") in orb.calls
    assert ("set_mode", "think") not in orb.calls


async def test_orb_is_shown_again_for_speaking_after_external_hide() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    orb.calls.clear()

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="THINKING")
    )
    # The overlay only switches to the speaking equalizer once audio is audible.
    await bridge._on_audio_out_first(AudioOutFirst())  # noqa: SLF001

    assert ("show", "speak") in orb.calls
    assert ("set_mode", "speak") not in orb.calls


async def test_speaking_keeps_thinking_wave_until_audio_is_audible() -> None:
    """The silent TTS-synthesis lead-in must read as thinking, not speaking.

    The supervisor flips to SPEAKING 0.5–2 s BEFORE the first audio sample
    leaves the speaker (TTS is still synthesizing). During that silence the
    overlay must keep the THINKING wave and only switch to the speaking
    equalizer bars once ``AudioOutFirst`` proves audio is actually audible.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    orb.calls.clear()

    # SPEAKING fires while TTS is still synthesizing → silence. The overlay
    # must NOT flip to the speaking bars or the talking 'nod' yet.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="THINKING")
    )
    assert ("show", "think") in orb.calls
    assert ("show", "speak") not in orb.calls
    assert ("play_animation", "nod") not in orb.calls
    orb.calls.clear()

    # First audible sample reached the speaker → NOW show the speaking bars.
    await bridge._on_audio_out_first(AudioOutFirst())  # noqa: SLF001
    assert ("show", "speak") in orb.calls
    assert ("play_animation", "nod") in orb.calls


async def test_orb_stays_visible_while_call_waits_for_next_turn() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="THINKING")
    )
    orb.calls.clear()

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="SPEAKING")
    )

    assert ("show", "listen") in orb.calls
    assert ("hide", None) not in orb.calls


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


async def test_zdf_subtitle_hallucination_is_not_shown_in_listening_bubble() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Untertitelung des ZDF, 2020", is_final=False)
    )

    assert ("show_listening_transcript", "Untertitelung des ZDF, 2020") not in orb.calls
    assert ("show_listening_transcript", "") in orb.calls


async def test_broadcast_boilerplate_is_not_shown_in_listening_bubble() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Eine Sendung des NDR, 2020", is_final=False)  # i18n-allow
    )

    assert ("show_listening_transcript", "Eine Sendung des NDR, 2020") not in orb.calls  # i18n-allow
    assert ("show_listening_transcript", "") in orb.calls


async def test_listening_bubble_mirrors_accumulating_pipeline_snapshots() -> None:
    """The pipeline emits accumulated snapshots (it merges probe tails
    internally via ``_merge_partial_transcript``). The bubble mirrors each
    snapshot 1:1 — exactly what the Desktop App's TranscriptionView does.
    Previously this test simulated raw probe deltas, but the pipeline does
    not emit deltas to the bus."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(
            text="Hallo ich moechte einen langen Prompt", is_final=False  # i18n-allow
        )
    )
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(
            text="Hallo ich moechte einen langen Prompt der weiter geht",  # i18n-allow
            is_final=False,
        )
    )

    assert orb.calls == [
        (
            "show_listening_transcript",
            "Hallo ich moechte einen langen Prompt",  # i18n-allow
        ),
        (
            "show_listening_transcript",
            "Hallo ich moechte einen langen Prompt der weiter geht",  # i18n-allow
        ),
    ]


async def test_listening_bubble_replaces_corrected_live_hypotheses() -> None:
    """When the pipeline corrects a hypothesis, the next snapshot replaces
    the previous one verbatim. The bubble follows — no leftover stale text
    leaking across the correction."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    snapshots = (
        "Was?",
        "Was ist morgens?",
        "Was ist morgen fuer ein Tag?",  # i18n-allow
        "Was ist morgen fuer einen Tag.",  # i18n-allow
    )
    for snapshot in snapshots:
        await bridge._on_transcription_update(  # noqa: SLF001
            TranscriptionUpdate(text=snapshot, is_final=False)
        )

    rendered = [t for (call, t) in orb.calls if call == "show_listening_transcript"]
    assert rendered == list(snapshots)
    assert orb.calls[-1] == (
        "show_listening_transcript",
        "Was ist morgen fuer einen Tag.",  # i18n-allow
    )


async def test_waiting_for_completion_does_not_clear_bubble_or_switch_to_think() -> None:
    """User paused on an incomplete fragment. The pipeline transitions to
    WAITING_FOR_COMPLETION; the orb must stay in listen-mode and the bubble
    text must remain intact — no premature "Denke nach …" indicator."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Öffne mal den", is_final=False)  # i18n-allow
    )
    orb.calls.clear()

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="WAITING_FOR_COMPLETION", previous="LISTENING")
    )

    assert ("show", "listen") in orb.calls
    assert ("show", "think") not in orb.calls
    # Bubble text not cleared
    assert bridge._listening_transcript_text == "Öffne mal den"  # noqa: SLF001  # i18n-allow
    assert bridge._completion_continuation is True  # noqa: SLF001


async def test_listening_after_waiting_for_completion_preserves_buffered_text() -> None:
    """Going LISTENING → WAITING_FOR_COMPLETION → LISTENING (next turn) is the
    classic pause/continuation flow. The transcript text must survive the
    re-entry into LISTENING; otherwise the user sees the bubble flash empty."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Öffne mal den", is_final=True)  # i18n-allow
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="WAITING_FOR_COMPLETION", previous="LISTENING")
    )
    orb.calls.clear()

    # Pipeline transitions back to LISTENING for the continuation — bubble
    # text must stay.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="WAITING_FOR_COMPLETION")
    )

    assert bridge._listening_transcript_text == "Öffne mal den"  # noqa: SLF001  # i18n-allow
    assert ("show_listening_transcript", "") not in orb.calls


async def test_transcription_update_accepted_during_waiting_for_completion() -> None:
    """The post-state TranscriptionUpdate the pipeline emits with the merged
    buffer fragment must reach the bubble while the state is
    WAITING_FOR_COMPLETION — that is how the bubble shows the so-far-spoken
    sentence across the pause."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="WAITING_FOR_COMPLETION", previous="LISTENING")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Öffne mal den", is_final=True)  # i18n-allow
    )

    assert ("show_listening_transcript", "Öffne mal den") in orb.calls  # i18n-allow


async def test_thinking_clears_completion_continuation_window() -> None:
    """Once the merged prompt is dispatched to the brain (THINKING), the
    continuation window must close so the NEXT user turn starts with a fresh
    empty bubble — otherwise stale text would bleed from one turn to the next."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="WAITING_FOR_COMPLETION", previous="LISTENING")
    )
    assert bridge._completion_continuation is True  # noqa: SLF001

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="WAITING_FOR_COMPLETION")
    )
    assert bridge._completion_continuation is False  # noqa: SLF001


async def test_final_transcription_update_replaces_partial_preview() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Was ist morgens?", is_final=False)
    )
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Was ist morgen fuer ein Tag?", is_final=True)  # i18n-allow
    )

    assert orb.calls[-1] == (
        "show_listening_transcript",
        "Was ist morgen fuer ein Tag?",  # i18n-allow
    )


async def test_bubble_walks_user_transcript_thinking_then_reply() -> None:
    """The orb bubble must walk the user through the whole turn.

    Earlier the bubble froze the *user* transcript across THINKING and
    SPEAKING, so the user never saw that Jarvis was thinking or what it
    answered (the user only ever saw their own words). That swung too far
    from the opposite bug, where random personality quips overwrote the
    transcript.

    Correct contract:
      LISTENING → the live user transcript (what you said)
      THINKING  → a thinking indicator while no reply text exists yet
      SPEAKING  → Jarvis's actual reply (mirrors the sidebar assistant line)

    Throughout, no random personality quip (``show_comment``) may appear —
    the bubble only ever renders meaningful turn content.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Kannst du mir helfen", is_final=True)
    )
    # While listening the bubble shows the user's words.
    assert ("show_listening_transcript", "Kannst du mir helfen") in orb.calls
    orb.calls.clear()

    # User stopped speaking → brain thinks. No reply yet, so the bubble must
    # show a thinking indicator — NOT the frozen user transcript.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    assert ("show_listening_transcript", THINKING_BUBBLE_TEXT) in orb.calls
    assert ("show_listening_transcript", "Kannst du mir helfen") not in orb.calls
    assert not any(call[0] == "show_comment" for call in orb.calls)
    orb.calls.clear()

    # Brain produced the reply.
    await bridge._on_response_generated(  # noqa: SLF001
        ResponseGenerated(text="Klar, ich helfe dir gerne.", language="de")
    )
    assert ("show_listening_transcript", "Klar, ich helfe dir gerne.") in orb.calls
    orb.calls.clear()

    # Jarvis starts speaking → the bubble shows Jarvis's reply, never a quip.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="THINKING")
    )
    await bridge._on_audio_out_first(AudioOutFirst())  # noqa: SLF001
    assert ("show_listening_transcript", "Klar, ich helfe dir gerne.") in orb.calls
    assert not any(call[0] == "show_comment" for call in orb.calls)


async def test_thinking_shows_indicator_even_without_a_captured_transcript() -> None:
    """If STT produced nothing usable, THINKING still shows the indicator
    rather than an empty bubble, so the user sees Jarvis is busy."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )

    assert ("show_listening_transcript", THINKING_BUBBLE_TEXT) in orb.calls


async def test_reply_arriving_during_speaking_updates_bubble() -> None:
    """ResponseGenerated can land after the SPEAKING transition (TTS races
    ahead of the brain event). The bubble must still pick up the reply."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="THINKING")
    )
    orb.calls.clear()

    await bridge._on_response_generated(  # noqa: SLF001
        ResponseGenerated(text="Hier ist deine Antwort.", language="de")
    )

    assert ("show_listening_transcript", "Hier ist deine Antwort.") in orb.calls


async def test_reply_is_reset_between_turns() -> None:
    """A reply from a previous turn must not leak into the next turn's
    THINKING bubble — each turn starts from the thinking indicator."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    # Turn 1 produces a reply.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )
    await bridge._on_response_generated(  # noqa: SLF001
        ResponseGenerated(text="Antwort aus Turn 1.", language="de")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="THINKING")
    )

    # Turn 2 starts — back to LISTENING, then THINKING with no new reply yet.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="SPEAKING")
    )
    orb.calls.clear()
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="THINKING", previous="LISTENING")
    )

    assert ("show_listening_transcript", THINKING_BUBBLE_TEXT) in orb.calls
    assert ("show_listening_transcript", "Antwort aus Turn 1.") not in orb.calls


# --- Boot visibility: the Jarvis Bar is the "you can speak now" affordance.
# It stays gated through warm-up and degraded UI-only releases, then maps once
# the genuine VoiceBootStatus arrives. Existing ungated surfaces retain their
# z-order repair for compatibility.


async def test_startup_gated_bar_releases_once_when_voice_is_usable() -> None:
    orb = _FakeStartupGatedBar()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )

    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="listening")
    )
    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="listening")
    )

    assert orb.calls.count(("release_startup_gate", None)) == 1
    assert not any(call[0] in {"show", "reassert_z_order"} for call in orb.calls)
    assert bridge._boot_visibility_released is True  # noqa: SLF001


@pytest.mark.parametrize(
    "event",
    [
        VoiceBootStatus(ready=False, detail="warmup_start"),
        VoiceBootStatus(ready=True, detail="voice_unavailable"),
        VoiceBootStatus(ready=True, detail="watchdog_timeout"),
    ],
)
async def test_non_usable_boot_status_does_not_release_bar(
    event: VoiceBootStatus,
) -> None:
    orb = _FakeStartupGatedBar()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )

    await bridge._on_voice_boot_status(event)  # noqa: SLF001

    assert ("release_startup_gate", None) not in orb.calls
    assert bridge._boot_visibility_released is False  # noqa: SLF001


async def test_genuine_ready_can_release_after_degraded_ui_ready() -> None:
    orb = _FakeStartupGatedBar()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )

    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="watchdog_timeout")
    )
    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="listening")
    )

    assert orb.calls.count(("release_startup_gate", None)) == 1


async def test_cached_boot_bar_releases_when_reselected_after_ready() -> None:
    bar = _FakeStartupGatedBar()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=bar, idle_animations_enabled=False, hide_on_idle=False
    )
    other_surface = _FakeOrb()

    # The user hides the bar during warm-up. Ready therefore lands while another
    # surface is current and cannot release the cached boot bar yet.
    bridge.set_surface(other_surface)
    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="listening")
    )
    assert bar.startup_gated is True

    bridge.set_surface(bar)

    assert bar.startup_gated is False
    assert bar.calls.count(("release_startup_gate", None)) == 1


async def test_non_persistent_bar_gate_releases_without_idle_show() -> None:
    orb = _FakeStartupGatedBar()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=True
    )

    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="listening")
    )

    assert ("release_startup_gate", None) in orb.calls
    assert not any(call[0] in {"show", "reassert_z_order"} for call in orb.calls)


async def test_ungated_persistent_bar_uses_explicit_z_order_reassert() -> None:
    orb = _FakeBarWithExplicitReassert()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )

    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="listening")
    )

    assert orb.calls.count(("reassert_z_order", None)) == 1
    assert not any(call[0] == "show" for call in orb.calls)


async def test_plain_legacy_persistent_surface_reasserts_current_mode() -> None:
    orb = _FakeOrb()
    orb._mode = "listen"
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )

    await bridge._on_voice_boot_status(  # noqa: SLF001
        VoiceBootStatus(ready=True, detail="listening")
    )

    assert orb.calls.count(("show", "listen")) == 1


@pytest.mark.parametrize("state", ["ERROR", "PAUSED"])
async def test_persistent_bar_shows_idle_on_non_active_state_never_hides(
    state: str,
) -> None:
    """An always-on bar (hide_on_idle=False) must survive a non-active state.

    A transient ERROR (an STT/provider hiccup) or a manual PAUSE used to hit the
    bridge's ``else: self._orb.hide()`` and withdraw the always-on bar until the
    next wake word — a second 'the bar vanishes' path on top of the
    minimise-to-tray one. A persistent bar must show the idle pill instead and
    NEVER hide."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state=state, previous="IDLE")
    )

    assert ("hide", None) not in orb.calls, f"{state} must not hide the always-on bar"
    assert ("show", "idle") in orb.calls, f"{state} must show the idle pill"


async def test_non_persistent_bar_hides_on_error_state() -> None:
    """The contrast: a hide-at-idle bar / the mascot still withdraws on a
    non-active state, unchanged."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=True
    )

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="ERROR", previous="IDLE")
    )

    assert ("hide", None) in orb.calls


async def test_listening_bubble_mirrors_pipeline_snapshot_one_to_one() -> None:
    """Pendel-Episode 3 regression (2026-05-27).

    The STT pipeline accumulates probe tails internally
    (``_merge_partial_transcript`` over ``_probe_live_text``) and publishes
    a *complete snapshot* in every ``TranscriptionUpdate``. The bubble must
    mirror that snapshot 1:1 — same source as the Desktop App's
    ``TranscriptionView`` (``setTranscription`` in
    ``useWebSocket.ts:138-140``, no second merge).

    The earlier bridge-side re-merge drifted from the TranscriptionView in
    two ways:

    * **Words missing** — when the pipeline downward-corrected
      (dropped a hallucinated prefix on a cleaner probe), the bridge's
      ``incoming in current`` heuristic kept the older, longer text.
    * **Words doubled** — when the bridge's word-overlap heuristic
      missed an overlap, the fallthrough concatenated current+incoming
      with a duplicate prefix.

    Both classes vanish once the bridge stops re-merging.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    # Pipeline-snapshot stream: growing accumulation, then a downward
    # correction (cleaner snapshot drops the hallucinated prefix).
    snapshots = [
        "Hallo",
        "Hallo wie",
        "Eine Sendung Hallo wie geht es dir",
        "Hallo wie geht es dir",          # downward correction
        "Hallo wie geht es dir heute",
    ]
    for snapshot in snapshots:
        await bridge._on_transcription_update(  # noqa: SLF001
            TranscriptionUpdate(text=snapshot, is_final=False)
        )

    rendered = [t for (call, t) in orb.calls if call == "show_listening_transcript"]
    assert rendered == snapshots, (
        "Bubble must mirror every pipeline snapshot 1:1; second-merge drift "
        f"detected. expected={snapshots!r} got={rendered!r}"
    )


async def test_listening_bubble_keeps_clean_snapshot_after_pipeline_downward_fix() -> None:
    """Concrete reproduction of the user-reported drift:

    Probe 1 hallucinates a leading boilerplate-ish prefix. The pipeline-side
    merge accepts it. Probe 2 is the clean version (hallucination dropped).
    Without this regression test the bridge keeps the dirty Probe 1 text
    because ``incoming in current`` is True and silently wins.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    orb.calls.clear()

    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Eine Sendung Hallo Jarvis", is_final=False)
    )
    await bridge._on_transcription_update(  # noqa: SLF001
        TranscriptionUpdate(text="Hallo Jarvis", is_final=False)
    )

    rendered = [t for (call, t) in orb.calls if call == "show_listening_transcript"]
    assert rendered[-1] == "Hallo Jarvis", (
        "Bubble kept stale longer snapshot after pipeline downward "
        f"correction. last_rendered={rendered[-1]!r}"
    )


async def test_stray_speaking_after_hangup_does_not_resurrect_orb() -> None:
    """Regression (2026-05-29): the mascot must disappear on "auflegen" and
    stay gone until the next wake.

    Repro from the live log: a brain reply that was already in-flight when the
    user said "auflegen" finishes ~4 s AFTER the session ended and SPEAKS its
    answer, emitting stray ``SPEAKING`` then ``LISTENING`` transitions. Those
    raw turn-state transitions must NOT bring the mascot back — the orb stays
    hidden until a genuine new ``VoiceSessionStarted``.

    Before the fix the stray ``SPEAKING`` re-showed the orb (and cancelled the
    pending grace-hide), and the trailing ``LISTENING`` left it stuck on screen
    with an empty "..." transcript bubble.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    # A real session: wake → listening.
    await bridge._on_session_started(  # noqa: SLF001
        VoiceSessionStarted(session_id="s1", wake_keyword="hey_jarvis")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    # User says "auflegen": session ends, supervisor falls back to IDLE.
    await bridge._on_session_ended(  # noqa: SLF001
        VoiceSessionEnded(session_id="s1", hangup_reason="voice_pattern")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="IDLE", previous="LISTENING")
    )
    orb.calls.clear()

    # Stray in-flight brain turn speaks AFTER the hangup, then re-listens.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="IDLE")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="SPEAKING")
    )

    # The mascot must NOT be resurrected by the stray transitions.
    assert ("show", "speak") not in orb.calls  # stray SPEAKING must not re-show
    assert ("show", "listen") not in orb.calls  # stray LISTENING must not re-show
    # The empty live-transcript bubble renders as "..." (overlay.py) — the exact
    # stuck-on-screen symptom from the report. It must not reappear either.
    assert ("show_listening_transcript", "") not in orb.calls


async def test_orb_shows_again_after_genuine_wake_following_hangup() -> None:
    """The suppression window must release on the next genuine wake.

    After the hangup + stray-transition suppression, a real new
    ``VoiceSessionStarted`` (the user calling "Hey Jarvis" again) must let the
    orb show normally — otherwise the fix would make the mascot never come
    back, which is the opposite failure.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_session_started(  # noqa: SLF001
        VoiceSessionStarted(session_id="s1")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )
    await bridge._on_session_ended(  # noqa: SLF001
        VoiceSessionEnded(session_id="s1", hangup_reason="voice_pattern")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="IDLE", previous="LISTENING")
    )
    # A stray transition gets suppressed.
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="SPEAKING", previous="IDLE")
    )
    orb.calls.clear()

    # Genuine new wake → orb must show again.
    await bridge._on_session_started(  # noqa: SLF001
        VoiceSessionStarted(session_id="s2", wake_keyword="hey_jarvis")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )

    assert ("show", "listen") in orb.calls


async def test_session_start_shows_listening_when_listening_state_is_deduped() -> None:
    """Live forensic (2026-06-21, session 1a3df62a): the bar only "woke up" at
    THINKING, never while the user spoke into it.

    Root cause: the supervisor's high-level state was already ``LISTENING`` when
    the new session started (a stale prior teardown left it there), so
    ``set_state("LISTENING")`` was a no-op and NO ``SystemStateChanged(LISTENING)``
    reached the bridge. The bridge then saw nothing until ``THINKING`` and only
    revealed/activated the bar there.

    ``VoiceSessionStarted`` is the authoritative "the user is being listened to
    now" signal — the bar must enter its listening look from THAT, never depend
    on a derived ``LISTENING`` state event that can be deduplicated upstream.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    # A genuine wake starts a session, but the supervisor was already LISTENING,
    # so NO SystemStateChanged(LISTENING) follows — the next state the bridge
    # would see is THINKING.
    await bridge._on_session_started(  # noqa: SLF001
        VoiceSessionStarted(session_id="s1", wake_keyword="hey_jarvis")
    )

    # The bar must already be in its listening look — before any THINKING.
    assert ("show", "listen") in orb.calls
    # A fresh turn opens an empty transcript bubble.
    assert ("show_listening_transcript", "") in orb.calls


async def test_confirmed_wake_word_pops_orb_before_session_start() -> None:
    """The first visual response should be tied to the confirmed wake event.

    ``VoiceSessionStarted`` is published by the state loop after wake handling.
    Waiting for it adds a small but visible delay after the selected wake phrase.
    ``WakeWordDetected`` is already emitted only after wake verification, so it
    is the earliest safe signal for the orb to appear.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_wake_word_detected(WakeWordDetected(keyword="hey_alex"))  # noqa: SLF001

    assert ("show", "listen") in orb.calls
    assert ("show_listening_transcript", "") not in orb.calls


def test_attach_subscribes_to_confirmed_wake_word_for_immediate_pop() -> None:
    bus = _RecordingBus()
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=bus, orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    bridge.attach()

    assert (WakeWordDetected, bridge._on_wake_word_detected) in bus.subscriptions  # noqa: SLF001


async def test_session_start_drives_mic_equalizer_immediately() -> None:
    """The bar's equalizer reacts to mic loudness only while the bridge
    considers the state LISTENING. After a session starts (even when the
    LISTENING state event is deduped upstream), mic loudness must drive the
    bars from the very first word — otherwise the bar looks dead while the user
    is talking."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_session_started(  # noqa: SLF001
        VoiceSessionStarted(session_id="s1", wake_keyword="hey_jarvis")
    )
    orb.calls.clear()

    bridge._on_mic_level(0.5)  # noqa: SLF001
    assert ("set_level", 0.5) in orb.calls


async def test_session_start_then_real_listening_does_not_double_show() -> None:
    """The normal flow (session start FOLLOWED by a genuine
    SystemStateChanged(LISTENING)) must enter the listening look exactly once —
    the genuine event is a clean same-state no-op, not a second show."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_session_started(  # noqa: SLF001
        VoiceSessionStarted(session_id="s1", wake_keyword="hey_jarvis")
    )
    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )

    assert orb.calls.count(("show", "listen")) == 1


async def test_active_state_shows_orb_before_any_session_event() -> None:
    """Backward-compat: with no VoiceSession lifecycle events seen yet, the
    bridge behaves exactly as before — an active state shows the orb. The
    suppression latch must default OFF so unit harnesses (and any non-mic
    surface) that drive _on_state directly are unaffected."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_state(  # noqa: SLF001
        SystemStateChanged(new_state="LISTENING", previous="IDLE")
    )

    assert ("show", "listen") in orb.calls


async def test_orb_pops_in_when_background_task_finishes_while_idle() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    await bridge._on_background_completed(JarvisAgentBackgroundCompleted(success=True))  # noqa: SLF001

    assert ("show", "speak") in orb.calls


async def test_delayed_hide_keeps_persistent_bar_visible_returns_to_idle() -> None:
    """A persistent "show at all times" bar must NEVER be withdrawn by the
    delayed hide that follows a background-task pop (``_on_background_completed``
    shows the bar in 'speak' then schedules ``_delayed_hide``). Withdrawing the
    always-on bar there was the "the bar vanishes after I talk to it, only the
    wake word brings it back" path — the SAME class as the consolidate
    restore-trap, via the one unconditional ``hide()`` the persistence gate in
    ``_on_state`` never covered. The persistent bar returns to the idle pill."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )  # type: ignore[arg-type]
    bridge._last_state = "IDLE"  # noqa: SLF001

    await bridge._delayed_hide(0.0)  # noqa: SLF001

    assert ("hide", None) not in orb.calls
    assert ("show", "idle") in orb.calls


async def test_delayed_hide_withdraws_non_persistent_bar() -> None:
    """Regression guard: a non-persistent bar / mascot (``hide_on_idle=True``)
    still withdraws on the delayed hide, so the salute/grace teardown after a
    real session is unchanged. The persistence gate only spares the always-on
    bar."""
    orb = _FakeOrb()
    bridge = OrbBusBridge(
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=True
    )  # type: ignore[arg-type]
    bridge._last_state = "IDLE"  # noqa: SLF001

    await bridge._delayed_hide(0.0)  # noqa: SLF001

    assert ("hide", None) in orb.calls
    assert ("show", "idle") not in orb.calls


async def test_attach_registers_mute_toggle_callback_with_orb() -> None:
    """``OrbBusBridge.attach()`` must inject a callback into the orb so
    a double-double-click can publish on the bus.

    Without this wiring the gesture would fire harmlessly (no callback
    registered branch), so the test asserts the contract by name.
    """
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]

    bridge.attach()

    set_calls = [c for c in orb.calls if c[0] == "set_on_mute_toggle"]
    assert len(set_calls) == 1
    assert callable(set_calls[0][1])


async def test_mute_toggle_publishes_voice_mute_request() -> None:
    """The callback ``OrbBusBridge`` registers must publish a
    ``VoiceMuteToggleRequested`` event with ``source="orb_dblclick_double"``
    on the real bus. Verified end-to-end via an in-process bus.
    """
    from jarvis.core.bus import EventBus
    from jarvis.core.events import VoiceMuteToggleRequested

    bus = EventBus()
    seen: list[VoiceMuteToggleRequested] = []
    bus.subscribe(VoiceMuteToggleRequested, lambda ev: seen.append(ev))

    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=bus, orb=orb, idle_animations_enabled=False)
    bridge.attach()
    callback = orb.mute_callback  # type: ignore[attr-defined]

    # The callback marshals onto a loop; running it inside the test
    # coroutine means the loop is already alive, so the
    # ``run_coroutine_threadsafe`` path is taken.
    callback()
    # Give the scheduled coro a tick to run.
    import asyncio
    await asyncio.sleep(0.05)

    assert len(seen) == 1
    assert seen[0].source == "orb_dblclick_double"


def test_orb_overlay_show_queues_work_without_cross_thread_tk_after() -> None:
    class _Root:
        def after(self, *_args, **_kwargs) -> None:
            raise AssertionError("cross-thread root.after must not be called")

        def deiconify(self) -> None:
            raise AssertionError("queued command should not run immediately")

    overlay = OrbOverlay()
    overlay._root = _Root()  # noqa: SLF001
    overlay._tk_thread_id = -1  # noqa: SLF001

    overlay.show(mode="listen")

    assert overlay._ui_queue.qsize() == 1  # noqa: SLF001


# ----------------------------------------------------------------------
# ADR-0016 — UserVisibleFeedback contract
# ----------------------------------------------------------------------


from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.events import (  # noqa: E402
    OrbResetRequested,
    UserVisibleFeedback,
)


async def test_attach_injects_feedback_publisher_into_orb() -> None:
    """The bridge must inject its publisher via ``set_feedback_publisher``
    so the orb can call back from the Tk thread."""
    class _OrbWithFeedback(_FakeOrb):
        def __init__(self) -> None:
            super().__init__()
            self.feedback_publisher = None

        def set_feedback_publisher(self, callback) -> None:
            self.feedback_publisher = callback

    orb = _OrbWithFeedback()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]
    bridge.attach()
    # Bound methods compare by their underlying function + instance, not
    # by identity — use == instead of `is`.
    assert orb.feedback_publisher == bridge._publish_visible_feedback  # noqa: SLF001


async def test_visible_feedback_publish_emits_event_with_correlation_id() -> None:
    """End-to-end L0: bridge publishes UserVisibleFeedback with the
    expected/observed pair and correlates back to the last
    SystemStateChanged trace_id."""
    bus = EventBus()
    seen: list[UserVisibleFeedback] = []

    async def _capture(event: UserVisibleFeedback) -> None:
        seen.append(event)

    bus.subscribe(UserVisibleFeedback, _capture)

    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=bus, orb=orb, idle_animations_enabled=False)

    # Drive a state-transition so _last_state_trace_id is populated.
    state_evt = SystemStateChanged(new_state="LISTENING", previous="IDLE")
    await bridge._on_state(state_evt)  # noqa: SLF001
    assert bridge._last_state_trace_id == str(state_evt.trace_id)  # noqa: SLF001

    # Simulate the orb's post-deiconify callback.
    observed = {"viewable": 1, "geometry": "108x108+2428+1285", "x": 2428, "y": 1285}
    bridge._publish_visible_feedback(mode="listen", observed=observed)  # noqa: SLF001

    # Drain any pending threadsafe publishes scheduled via asyncio.
    import asyncio
    await asyncio.sleep(0.05)

    assert len(seen) >= 1
    evt = seen[-1]
    assert evt.surface == "orb"
    assert evt.expected == {"mode": "listen", "viewable": True}
    assert evt.observed == observed
    assert evt.correlation_id == str(state_evt.trace_id)


async def test_reset_requested_dispatches_to_tk_thread() -> None:
    """L2 wiring: an OrbResetRequested bus event must call into the
    orb's ``_on_reset_double_click`` via ``root.after(0, ...)``."""
    class _Root:
        def __init__(self) -> None:
            self.after_calls: list = []

        def after(self, delay: int, callback) -> None:
            self.after_calls.append((delay, callback))
            # Immediately run the callback so the test can assert.
            callback()

    class _OrbWithReset(_FakeOrb):
        def __init__(self) -> None:
            super().__init__()
            self._root = _Root()
            self.reset_called: list = []

        def _on_reset_double_click(self, event) -> None:
            self.reset_called.append(event)

    orb = _OrbWithReset()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]
    await bridge._on_reset_requested(OrbResetRequested(source="voice"))  # noqa: SLF001

    assert len(orb._root.after_calls) == 1
    assert orb._root.after_calls[0][0] == 0
    assert orb.reset_called == [None]


async def test_reset_requested_with_no_root_logs_and_returns() -> None:
    """Defensive: when the orb has no Tk root yet (boot race), the reset
    request must NOT crash — the handler logs and returns."""
    class _OrbNoRoot(_FakeOrb):
        _root = None

        def _on_reset_double_click(self, event) -> None:  # pragma: no cover
            raise AssertionError("must not be called when _root is None")

    orb = _OrbNoRoot()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]
    # Must not raise:
    await bridge._on_reset_requested(OrbResetRequested(source="test"))  # noqa: SLF001
