"""OrbBusBridge → desktop pet (docs/pets.md).

The bridge drives every overlay style; the pet adds optional surface methods
the bridge reaches through ``getattr``. These tests use a real ``EventBus`` and
a hand-written pet surface and pin: the speaker-mute mirror, the shortcut, the
one-shot outcomes, the rate-limited status lines (pet only), the pen control,
the per-surface re-wiring on a live swap, and that the pet is never hidden
when Jarvis goes idle.
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
    ComposeRequested,
    ErrorOccurred,
    JarvisAgentAnnouncement,
    JarvisAgentBackgroundCompleted,
    PetVisibilityToggleRequested,
    SpeechSpoken,
    SystemStateChanged,
    TranscriptionUpdate,
    UiLanguageChanged,
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

    def show_status(self, header: str, line: str) -> None:
        self.calls.append(("status", header, line))

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


@pytest.fixture()
def fast_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the status rate limit so the flush path runs in milliseconds."""
    monkeypatch.setattr(bus_bridge, "PET_STATUS_MIN_INTERVAL_S", 0.02)


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
        (ErrorOccurred(layer="brain", message="boom"), "error"),
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
# status lines
# ---------------------------------------------------------------------------


async def test_status_lines_reach_only_a_surface_that_wants_them() -> None:
    bar = _Surface()  # has show_status, but does not ask for lines
    _bridge_, bus = _bridge(bar)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    await bus.publish(ActionProposed(tool_name="web_search", rationale="Checking the news"))
    await bus.publish(AssistantTextDelta(text="Here it is.", done=True))
    assert bar.of("status") == []


async def test_thinking_shows_the_header_then_the_reason(fast_status) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    assert pet.of("status")[-1] == ("status", "Thinking …", "")

    await asyncio.sleep(0.03)
    await bus.publish(
        ActionProposed(tool_name="web_search", rationale="Looking up the weather in Berlin")
    )
    assert pet.of("status")[-1] == ("status", "Thinking …", "Looking up the weather in Berlin")


async def test_a_tool_without_a_reason_shows_its_name(fast_status) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(ActionProposed(tool_name="mcp__github__create_issue"))
    assert pet.of("status")[-1] == ("status", "Thinking …", "Create issue")


async def test_progress_announcements_are_thinking_lines(fast_status) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(AnnouncementRequested(text="Step 2 of 5 done.", kind="progress"))
    await asyncio.sleep(0.03)
    await bus.publish(AnnouncementRequested(text="All finished.", kind="completion"))
    await asyncio.sleep(0.03)
    # A spawn announcement's action is a bare clause fragment in the spawn
    # tool's language — never a status line on its own.
    await bus.publish(
        JarvisAgentAnnouncement(action="eine Flask-App baut", target="auf Port 8000")  # i18n-allow
    )
    assert [c[2] for c in pet.of("status")] == ["Step 2 of 5 done."]


async def test_a_streamed_reply_shows_its_latest_complete_sentence(fast_status) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(AssistantTextDelta(text="It is sunny. Tomorrow it"))
    assert pet.of("status")[-1] == ("status", "Answer", "It is sunny.")

    await asyncio.sleep(0.03)
    await bus.publish(AssistantTextDelta(text="It is sunny. Tomorrow it rains.", done=True))
    assert pet.of("status")[-1] == ("status", "Answer", "Tomorrow it rains.")


async def test_the_final_snapshot_beats_the_rate_limit() -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(AssistantTextDelta(text="First."))
    await bus.publish(AssistantTextDelta(text="First. Second.", done=True))
    assert [c[2] for c in pet.of("status")] == ["First.", "Second."]


async def test_a_throttled_line_is_flushed_after_the_interval(fast_status) -> None:
    pet = _Pet()
    bridge, bus = _bridge(pet)
    await bus.publish(VoiceSessionStarted(session_id="s1"))
    bridge._last_state = "LISTENING"  # noqa: SLF001
    await bus.publish(TranscriptionUpdate(text="what is", is_final=False))
    await bus.publish(TranscriptionUpdate(text="what is the weather", is_final=False))
    await asyncio.sleep(0.08)
    assert pet.of("status")[-1] == ("status", "Listening …", "what is the weather")


async def test_a_new_session_starts_a_fresh_bubble() -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(VoiceSessionStarted(session_id="s1"))
    await bus.publish(VoiceSessionStarted(session_id="s2"))
    # The same header twice: the reset means the second is not a "repeat".
    assert pet.of("status") == [
        ("status", "Listening …", ""),
        ("status", "Listening …", ""),
    ]


async def test_headers_follow_the_interface_language(fast_status) -> None:
    pet = _Pet()
    _bridge_, bus = _bridge(pet, language="de")
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    assert pet.of("status")[-1][1] == "Denke nach …"  # i18n-allow

    await bus.publish(UiLanguageChanged(language="es"))
    await asyncio.sleep(0.03)
    await bus.publish(AssistantTextDelta(text="Hola.", done=True))  # i18n-allow
    assert pet.of("status")[-1][1] == "Respuesta"  # i18n-allow


async def test_the_language_falls_back_to_the_pipeline_config(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_refs,
        "_SPEECH_PIPELINE",
        [_FakePipeline(muted=False, volume=1.0, language="es")],
    )
    pet = _Pet()
    _bridge_, bus = _bridge(pet)
    await bus.publish(SystemStateChanged(previous="IDLE", new_state="THINKING"))
    assert pet.of("status")[-1][1] == "Pensando …"  # i18n-allow


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
