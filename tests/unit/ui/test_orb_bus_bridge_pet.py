"""OrbBusBridge → desktop pet (docs/pets.md).

The bridge drives every overlay style; the pet adds optional surface methods
the bridge reaches through ``getattr``. These tests use a real ``EventBus`` and
a hand-written pet surface and pin: the speaker-mute mirror, the shortcut, the
one-shot outcomes, the thinking card (pet only — real thinking, never the
transcript or the reply), the pen control, the per-surface re-wiring on a
live swap, and that the pet is never hidden when Jarvis goes idle.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionExecuted,
    ActionProposed,
    AnnouncementRequested,
    AssistantTextDelta,
    AudioOutFirst,
    ComposeRequested,
    ErrorOccurred,
    JarvisAgentAnnouncement,
    JarvisAgentBackgroundCompleted,
    JarvisAgentTaskCompleted,
    JarvisAgentTaskStarted,
    PetVisibilityToggleRequested,
    ReasoningSummaryUpdated,
    ResponseGenerated,
    SpeechSpoken,
    SystemStateChanged,
    ToolCallStarted,
    TranscriptionUpdate,
    UiLanguageChanged,
    VoiceBootStatus,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceSpeakerMuteChanged,
    WakeCandidateDetected,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

try:  # noqa: SIM105 — intentional try-import for the discovery quirk
    from ui.orb import bus_bridge  # type: ignore[import-not-found]
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip("ui.orb not available on the pytest PYTHONPATH.", allow_module_level=True)


class _Surface:
    """Records every call a surface can receive from the bridge."""

    wants_status_lines = False
    keeps_visible_when_idle = False

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.callbacks: dict[str, object] = {}
        self._mode = "idle"

    # the four-mode surface API every style has
    def show(self, mode: str = "listen") -> None:
        self._mode = mode
        self.calls.append(("show", mode))

    def hide(self) -> None:
        self.calls.append(("hide",))

    def play_animation(self, name: str) -> None:
        self.calls.append(("play_animation", name))

    def stop_animation(self, name: str) -> None:
        self.calls.append(("stop_animation", name))

    def set_level(self, level: float) -> None:
        self.calls.append(("set_level", level))

    def show_listening_transcript(self, text: str, duration_ms: int) -> None:
        self.calls.append(("transcript", text))

    # callback setters
    def set_on_mute_toggle(self, cb) -> None:
        self.callbacks["mute"] = cb

    def set_on_prompt_mode_toggle(self, cb) -> None:
        self.callbacks["prompt_mode"] = cb

    def set_on_compose(self, cb) -> None:
        self.callbacks["compose"] = cb

    def set_feedback_publisher(self, cb) -> None:
        self.callbacks["feedback"] = cb

    def set_on_show_window(self, cb) -> None:
        self.callbacks["show_window"] = cb

    # mirrors
    def set_muted(self, muted: bool) -> None:
        self.calls.append(("set_muted", muted))

    def set_speaker_muted(self, muted: bool) -> None:
        self.calls.append(("set_speaker_muted", muted))

    def set_prompt_mode(self, enabled: bool, paused: bool) -> None:
        self.calls.append(("set_prompt_mode", enabled, paused))

    # pet-only methods (a bar may have show_status too; the gate is the flag)
    def set_pet_outcome(self, kind: str) -> None:
        self.calls.append(("outcome", kind))

    def show_status(self, title: str, detail: str = "") -> None:
        self.calls.append(("status", title, detail))

    def clear_status(self, linger_s: float = 1.5) -> None:
        self.calls.append(("clear", linger_s))

    def toggle_visible(self) -> None:
        self.calls.append(("toggle_visible",))

    def of(self, name: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == name]


class _Pet(_Surface):
    wants_status_lines = True
    keeps_visible_when_idle = True


class _FakePipeline:
    """What the bridge reads off the live pipeline when it seeds a surface."""

    def __init__(self, *, muted: bool, volume: float, language: str = "en") -> None:
        self.is_muted = muted
        self._volume = volume
        self._dictation_cfg = None

        class _Ui:
            pass

        class _Cfg:
            ui = _Ui()

        _Cfg.ui.language = language
        self._config = _Cfg()

    def get_tts_volume(self) -> float:
        return self._volume


@pytest.fixture(autouse=True)
def _no_live_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_refs, "_SPEECH_PIPELINE", [])


def _bridge(surface: _Surface, **kwargs) -> tuple[OrbBusBridge, EventBus]:
    bus = EventBus()
    bridge = OrbBusBridge(bus=bus, orb=surface, **kwargs)  # type: ignore[arg-type]
    bridge.attach()
    return bridge, bus


# ---------------------------------------------------------------------------
# mirrors, shortcut, pen
# ---------------------------------------------------------------------------


async def test_speaker_mute_is_mirrored() -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(VoiceSpeakerMuteChanged(muted=True, source="pet"))
    await bus.publish(VoiceSpeakerMuteChanged(muted=False, source="settings"))
    assert pet.of("set_speaker_muted") == [
        ("set_speaker_muted", True),
        ("set_speaker_muted", False),
    ]


async def test_shortcut_toggles_the_pet() -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(PetVisibilityToggleRequested(source="hotkey"))
    assert pet.of("toggle_visible") == [("toggle_visible",)]


async def test_surfaces_without_pet_methods_ignore_every_pet_event() -> None:
    class _Bar:
        def show(self, mode: str = "listen") -> None:
            pass

        def hide(self) -> None:
            pass

        def set_level(self, level: float) -> None:
            pass

    _bridge_, bus = _bridge(_Bar())  # type: ignore[arg-type]
    await bus.publish(PetVisibilityToggleRequested(source="hotkey"))
    await bus.publish(VoiceSpeakerMuteChanged(muted=True))
    await bus.publish(ActionExecuted(tool_name="x", success=True))
    await bus.publish(ActionProposed(tool_name="web_search", rationale="Looking it up"))


def test_attach_seeds_both_mutes_from_the_live_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_refs, "_SPEECH_PIPELINE", [_FakePipeline(muted=True, volume=0.0)]
    )
    pet = _Pet()
    _bridge(pet)
    assert ("set_muted", True) in pet.calls
    assert ("set_speaker_muted", True) in pet.calls


def test_pen_publishes_compose_on_the_backend_loop() -> None:
    backend = asyncio.new_event_loop()
    thread = threading.Thread(target=backend.run_forever, daemon=True)
    thread.start()
    try:
        seen: list[tuple[ComposeRequested, asyncio.AbstractEventLoop]] = []
        done = threading.Event()
        bus = EventBus()

        async def _record(event: ComposeRequested) -> None:
            seen.append((event, asyncio.get_running_loop()))
            done.set()

        bus.subscribe(ComposeRequested, _record)
        pet = _Pet()
        bridge = OrbBusBridge(bus=bus, orb=pet)  # type: ignore[arg-type]
        bridge.attach()
        bridge._loop = backend  # noqa: SLF001 — what attach() captures in the app

        pet.callbacks["compose"]()  # fired from this (loop-less) "Tk" thread

        assert done.wait(timeout=2.0)
        assert seen[0][0].source == "pet"
        assert seen[0][1] is backend
    finally:
        backend.call_soon_threadsafe(backend.stop)
        thread.join(timeout=2.0)
        backend.close()


def test_a_swapped_in_surface_gets_every_callback_and_the_live_mutes(monkeypatch) -> None:
    old, new = _Surface(), _Pet()
    bridge = OrbBusBridge(bus=EventBus(), orb=old)  # type: ignore[arg-type]
    bridge.attach()
    monkeypatch.setattr(
        runtime_refs, "_SPEECH_PIPELINE", [_FakePipeline(muted=False, volume=0.0)]
    )

    bridge.set_surface(new)

    for name in ("mute", "prompt_mode", "compose", "feedback", "show_window"):
        assert name in new.callbacks, name
    assert ("set_muted", False) in new.calls
    assert ("set_speaker_muted", True) in new.calls


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,expected",
    [
        (ActionExecuted(tool_name="open_app", success=True), "success"),
        (ActionExecuted(tool_name="open_app", success=False), "error"),
        (SpeechSpoken(text="Done.", spoken_kind="action_done"), "success"),
        (SpeechSpoken(text="Finished.", spoken_kind="completion"), "success"),
        (SpeechSpoken(text="Too slow.", spoken_kind="timeout"), "error"),
        (SpeechSpoken(text="Offline.", spoken_kind="unavailable"), "error"),
        (SpeechSpoken(text="Can't hear.", spoken_kind="stt_unavailable"), "error"),
        (JarvisAgentBackgroundCompleted(success=True), "success"),
        (ErrorOccurred(layer="brain", message="boom", recoverable=False), "error"),
        (VoiceSessionEnded(session_id="s", hangup_reason="error"), "error"),
    ],
)
async def test_outcomes_play_the_one_shot(event, expected: str) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(event)
    assert pet.of("outcome") == [("outcome", expected)]


@pytest.mark.parametrize(
    "event",
    [
        SpeechSpoken(text="Hello.", spoken_kind="reply"),
        JarvisAgentBackgroundCompleted(success=False),
        VoiceSessionEnded(session_id="s", hangup_reason="hotkey"),
        VoiceSessionEnded(session_id="s", hangup_reason="idle_timeout"),
    ],
)
async def test_ordinary_events_play_no_outcome(event) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(event)
    assert pet.of("outcome") == []


# ---------------------------------------------------------------------------
# the thinking card: only real thinking, title + detail
# ---------------------------------------------------------------------------


@pytest.fixture()
def fast_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink every card timer so the timed paths run in milliseconds."""
    monkeypatch.setattr(bus_bridge, "PET_STATUS_MIN_INTERVAL_S", 0.02)
    monkeypatch.setattr(bus_bridge, "PET_CARD_THINKING_FALLBACK_S", 0.03)
    monkeypatch.setattr(bus_bridge, "PET_CARD_QUIET_CLEAR_S", 0.03)
    monkeypatch.setattr(bus_bridge, "PET_CARD_TASK_DONE_S", 0.03)


_PLAN = "**Planning the trip**\n\nI need the train times first. Then I compare"


async def test_the_card_reaches_only_a_surface_that_wants_it(fast_card) -> None:
    bar = _Surface()  # has show_status, but does not ask for the card
    _bridge_, bus = _bridge(bar)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text=_PLAN))
    await bus.publish(ActionProposed(tool_name="web_search", rationale="Checking the news"))
    await bus.publish(JarvisAgentTaskStarted(utterance="Build a site"))
    await asyncio.sleep(0.06)
    assert bar.of("status") == []
    assert bar.of("clear") == []


async def test_thinking_shows_no_card_until_a_thought_arrives(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    assert pet.of("status") == []

    await bus.publish(ReasoningSummaryUpdated(response_id="r", text=_PLAN))
    # The section heading is the title; the last COMPLETE sentence the detail.
    assert pet.of("status") == [("status", "Planning the trip", "I need the train times first.")]


async def test_a_silent_thinking_phase_gets_the_bare_title(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await asyncio.sleep(0.06)
    assert pet.of("status") == [("status", "Thinking …", "")]


async def test_a_quick_turn_shows_no_card_at_all(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(SystemStateChanged(previous="THINKING", new_state="SPEAKING"))
    await asyncio.sleep(0.06)
    assert pet.of("status") == []


async def test_a_tool_step_runs_under_the_current_thought(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text=_PLAN))
    await asyncio.sleep(0.03)
    await bus.publish(
        ActionProposed(tool_name="web_search", rationale="Looking up the trains to Berlin")
    )
    assert pet.of("status")[-1] == (
        "status",
        "Planning the trip",
        "Looking up the trains to Berlin",
    )


async def test_a_tool_without_a_reason_shows_its_name(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ActionProposed(tool_name="mcp__github__create_issue"))
    assert pet.of("status")[-1] == ("status", "Working", "Create issue")
    await asyncio.sleep(0.03)
    await bus.publish(ToolCallStarted(tool_name="read_file"))
    assert pet.of("status")[-1] == ("status", "Working", "Read file")


async def test_a_tool_result_is_the_next_step(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet, language="de")
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ActionProposed(tool_name="run_command", rationale="Running the tests"))
    await asyncio.sleep(0.03)
    await bus.publish(ActionExecuted(tool_name="run_command", success=True))
    assert pet.of("status")[-1] == ("status", "Arbeitet", "Befehl ausgeführt")  # i18n-allow


async def test_a_tool_result_without_a_card_puts_none_up(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ActionExecuted(tool_name="run_command", success=True))
    assert pet.of("status") == []


async def test_transcripts_replies_and_spoken_lines_never_reach_the_card(fast_card) -> None:
    pet = _Pet()
    bridge, bus = _bridge(pet)
    await bus.publish(VoiceSessionStarted(session_id="s1"))
    bridge._last_state = "LISTENING"  # noqa: SLF001
    await bus.publish(TranscriptionUpdate(text="what is the weather", is_final=True))
    await bus.publish(AnnouncementRequested(text="Step 2 of 5 done.", kind="progress"))
    await bus.publish(AnnouncementRequested(text="One moment.", kind="preamble"))
    await bus.publish(AssistantTextDelta(channel="voice", text="It is sunny.", done=True))
    await bus.publish(ResponseGenerated(text="It is sunny."))
    await bus.publish(
        JarvisAgentAnnouncement(action="eine Flask-App baut", target="auf Port 8000")  # i18n-allow
    )
    await asyncio.sleep(0.06)
    assert pet.of("status") == []
    assert pet.of("transcript") == []


async def test_audible_speech_takes_the_card_down(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text=_PLAN, done=True))
    await bus.publish(SystemStateChanged(previous="THINKING", new_state="SPEAKING"))
    assert pet.of("clear") == []  # synthesis lead-in: still thinking for the user
    await bus.publish(AudioOutFirst())
    assert pet.of("clear") == [("clear", bus_bridge.PET_CARD_LINGER_S)]


@pytest.mark.parametrize("next_state", ["IDLE", "LISTENING", "ERROR"])
async def test_the_end_of_thinking_takes_the_card_down(fast_card, next_state: str) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ActionProposed(tool_name="web_search", rationale="Searching"))
    await bus.publish(SystemStateChanged(previous="THINKING", new_state=next_state))
    assert len(pet.of("clear")) == 1


async def test_a_thought_while_jarvis_already_talks_clears_itself(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="SPEAKING"))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text=_PLAN))
    assert pet.of("status")
    await asyncio.sleep(0.06)
    assert len(pet.of("clear")) == 1


async def test_an_agent_task_keeps_its_card_until_it_is_done(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    started = JarvisAgentTaskStarted(utterance="Improve the Jarvis agents")
    await bus.publish(started)
    assert pet.of("status")[-1] == ("status", "Improve the Jarvis agents", "Working …")

    # The turn that asked for it ends; the agent works on.
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="SPEAKING"))
    await bus.publish(AudioOutFirst())
    await bus.publish(SystemStateChanged(previous="SPEAKING", new_state="IDLE"))
    await asyncio.sleep(0.06)
    assert pet.of("clear") == []

    await bus.publish(JarvisAgentTaskCompleted(trace_id=started.trace_id, success=True))
    assert pet.of("status")[-1] == ("status", "Improve the Jarvis agents", "Done")
    await asyncio.sleep(0.06)
    assert len(pet.of("clear")) == 1


async def test_a_completion_under_another_trace_still_releases_the_card(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(JarvisAgentTaskStarted(utterance="Build a site"))
    await bus.publish(JarvisAgentTaskCompleted(success=False))
    assert pet.of("status")[-1] == ("status", "Build a site", "Failed")
    await asyncio.sleep(0.06)
    assert len(pet.of("clear")) == 1


async def test_a_lost_completion_does_not_pin_the_card_forever(fast_card) -> None:
    pet = _Pet()
    bridge, bus = _bridge(pet)
    clock = _Clock()
    bridge._clock = clock  # noqa: SLF001 — the task age is measured on it
    await bus.publish(JarvisAgentTaskStarted(utterance="Build a site"))
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="LISTENING"))
    assert pet.of("clear") == []
    clock.now += bus_bridge.PET_CARD_TASK_MAX_S + 1
    await bus.publish(SystemStateChanged(previous="LISTENING", new_state="IDLE"))
    assert len(pet.of("clear")) == 1


async def test_the_final_snapshot_beats_the_rate_limit() -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text="**Plan**\n\nFirst."))
    await bus.publish(
        ReasoningSummaryUpdated(response_id="r", text="**Plan**\n\nFirst. Second.", done=True)
    )
    assert [c[2] for c in pet.of("status")] == ["First.", "Second."]


async def test_a_throttled_card_is_flushed_after_the_interval(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text="**Plan**\n\nFirst."))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text="**Plan**\n\nFirst. Second."))
    await asyncio.sleep(0.06)
    assert pet.of("status")[-1] == ("status", "Plan", "Second.")


async def test_a_new_session_clears_the_previous_card(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ReasoningSummaryUpdated(response_id="r", text=_PLAN))
    await bus.publish(VoiceSessionStarted(session_id="s2"))
    assert len(pet.of("clear")) == 1
    assert pet.of("status") == [("status", "Planning the trip", "I need the train times first.")]


async def test_labels_follow_the_interface_language(fast_card) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet, language="de")
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await asyncio.sleep(0.06)
    assert pet.of("status")[-1] == ("status", "Denkt nach …", "")  # i18n-allow

    await bus.publish(UiLanguageChanged(language="es"))
    await bus.publish(ActionProposed(tool_name="web_search"))
    # Shown at once, or by the flush when it landed inside the rate limit.
    await asyncio.sleep(0.06)
    assert pet.of("status")[-1] == ("status", "Trabajando", "Web search")  # i18n-allow


async def test_the_language_falls_back_to_the_pipeline_config(monkeypatch, fast_card) -> None:
    monkeypatch.setattr(
        runtime_refs,
        "_SPEECH_PIPELINE",
        [_FakePipeline(muted=False, volume=1.0, language="es")],
    )
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await asyncio.sleep(0.06)
    assert pet.of("status")[-1] == ("status", "Pensando …", "")  # i18n-allow


# ---------------------------------------------------------------------------
# idle visibility
# ---------------------------------------------------------------------------


async def test_the_pet_stays_on_screen_when_a_preview_is_retracted() -> None:
    """``hide_on_idle`` is True for every non-bar style; the pet overrides it."""
    pet = _Pet()
    _bridge_, bus = _bridge(pet, hide_on_idle=True)
    await bus.publish(WakeCandidateDetected(active=True))
    await bus.publish(WakeCandidateDetected(active=False))
    assert pet.of("hide") == []
    assert pet.calls[-1] == ("show", "idle")


async def test_a_mascot_still_hides_when_a_preview_is_retracted() -> None:
    mascot = _Surface()
    _bridge_, bus = _bridge(mascot, hide_on_idle=True)
    await bus.publish(WakeCandidateDetected(active=True))
    await bus.publish(WakeCandidateDetected(active=False))
    assert mascot.calls[-1] == ("hide",)


# ---------------------------------------------------------------------------
# outcomes report what the user experiences (review fixes)
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_a_recoverable_error_plays_nothing() -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(ErrorOccurred(layer="brain", message="retrying", recoverable=True))
    assert pet.of("outcome") == []


@pytest.mark.parametrize("success", [True, False])
async def test_a_tool_result_inside_a_turn_is_not_an_outcome(success: bool) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ActionExecuted(tool_name="web_search", success=success))
    assert pet.of("outcome") == []


async def test_outcomes_are_throttled_but_an_error_after_a_success_shows() -> None:
    pet = _Pet()
    bridge, bus = _bridge(pet)
    clock = _Clock()
    bridge._clock = clock  # noqa: SLF001 — the throttle's clock
    await bus.publish(JarvisAgentBackgroundCompleted(success=True))
    clock.now += 1.0
    await bus.publish(SpeechSpoken(text="Done.", spoken_kind="action_done"))
    clock.now += 0.5
    await bus.publish(SpeechSpoken(text="Too slow.", spoken_kind="timeout"))
    clock.now += 0.5
    await bus.publish(ErrorOccurred(layer="brain", message="x", recoverable=False))
    clock.now += 3.5
    await bus.publish(SpeechSpoken(text="Finished.", spoken_kind="completion"))
    assert [c[1] for c in pet.of("outcome")] == ["success", "error", "success"]


# ---------------------------------------------------------------------------
# the backend loop: pinned early, never replaced by a throwaway one
# ---------------------------------------------------------------------------


async def test_the_voice_ready_signal_pins_the_backend_loop() -> None:
    pet = _Pet()
    bridge, bus = _bridge(pet)
    bridge._loop = None  # noqa: SLF001 — as if attach() had run off the loop
    await bus.publish(VoiceBootStatus(ready=True))
    assert bridge._loop is asyncio.get_running_loop()  # noqa: SLF001


def test_a_gesture_after_the_backend_loop_stopped_is_dropped() -> None:
    bus = EventBus()
    seen: list[ComposeRequested] = []

    async def _record(event: ComposeRequested) -> None:
        seen.append(event)

    bus.subscribe(ComposeRequested, _record)
    pet = _Pet()
    bridge = OrbBusBridge(bus=bus, orb=pet)  # type: ignore[arg-type]
    bridge.attach()
    stopped = asyncio.new_event_loop()
    try:
        bridge._loop = stopped  # noqa: SLF001 — captured once, no longer running
        pet.callbacks["compose"]()
        # Running the publish on a throwaway loop instead is the 2026-06-28
        # cross-loop crash; the gesture is dropped.
        assert seen == []
    finally:
        stopped.close()
