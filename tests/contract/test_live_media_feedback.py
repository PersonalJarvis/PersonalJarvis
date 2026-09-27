"""Portable media-to-native-bar contract, without a device or provider account."""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.audio import level_tap, mic_level
from jarvis.core.bus import EventBus
from jarvis.core.events import AudioOutFirst, SystemStateChanged
from jarvis.live.media import MediaLevels
from jarvis.live.native import NativeLiveVoiceSession
from jarvis.live.session import LiveVoiceSession
from tests.fakes.voice_surface import VoiceSurface
from ui.orb.bus_bridge import OrbBusBridge


def snapshot(*, input_level=0.0, output_level=0.0, input_active=False, playback_active=False):
    return dict(
        type="media_levels",
        input_level=input_level,
        output_level=output_level,
        input_active=input_active,
        playback_active=playback_active,
    )


@pytest.fixture(params=[LiveVoiceSession, NativeLiveVoiceSession])
def presentation(request):
    surface = VoiceSurface()
    bus = EventBus()
    frames, events = [], []
    bridge = OrbBusBridge(bus, surface, idle_animations_enabled=False)
    bus.subscribe(SystemStateChanged, bridge._on_state)
    bus.subscribe(AudioOutFirst, bridge._on_audio_out_first)

    async def record(event):
        events.append((type(event).__name__, getattr(event, "new_state", None)))

    async def send(frame):
        frames.append(frame)

    bus.subscribe(SystemStateChanged, record)
    bus.subscribe(AudioOutFirst, record)
    session = request.param(
        session_id="media-test",
        bus=bus,
        send_json=send,
        send_binary=send,
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        providers=[SimpleNamespace(name="test")],
    )
    session._connection = object()
    # Amplitude must never visit a ledger or execute a tool.
    session._ledger = object()
    mic_level.reset_for_tests()
    level_tap.reset()
    unsub_mic = mic_level.subscribe(bridge._on_mic_level)
    unsub_output = level_tap.subscribe(bridge._note_tts_level)
    yield session, surface, frames, events
    session._closing = True
    session._clear_media_levels()
    unsub_mic()
    unsub_output()
    level_tap.reset()
    mic_level.reset_for_tests()


@pytest.mark.asyncio
async def test_native_microphone_meter_remains_visible_while_connecting(presentation):
    _session, surface, _frames, _events = presentation
    bus = _session._bus
    await bus.publish(SystemStateChanged(new_state="LISTENING", previous="IDLE"))
    await bus.publish(SystemStateChanged(new_state="CONNECTING", previous="LISTENING"))
    mic_level.publish(0.65)
    assert surface.mode == "listen"
    assert surface.level == pytest.approx(0.65)


@pytest.mark.asyncio
async def test_interim_speech_work_and_reply_reach_the_actual_surface(presentation):
    session, surface, frames, events = presentation
    await session.handle_control(snapshot(input_level=0.61, input_active=True))
    assert surface.mode == "listen"
    assert surface.level == pytest.approx(0.61)
    mic_level.feed(0.0)  # An idle native wake detector cannot erase browser levels.
    assert surface.level == pytest.approx(0.61)
    await session.handle_control(snapshot())
    await session._note_thinking()
    session._response_revisions["work"] = 0
    await session.handle_control(snapshot(output_level=0.74, playback_active=True))
    assert surface.mode == "speak"  # SPEAKING alone leaves the native bar thinking.
    assert surface.level == pytest.approx(0.74)
    assert events.index(("SystemStateChanged", "SPEAKING")) < events.index(("AudioOutFirst", None))
    await session._note_thinking()
    assert surface.mode == "speak"
    assert frames[-1]["type"] == "speaking"
    await session.handle_control(snapshot())
    assert surface.mode == "think"
    # A correction is visible immediately without cancelling background work.
    await session.handle_control(snapshot(input_level=0.48, input_active=True))
    assert surface.mode == "listen"
    assert surface.level == pytest.approx(0.48)
    await session.handle_control(snapshot())
    assert surface.mode == "think"
    await session.handle_control(snapshot(output_level=0.82, playback_active=True))
    assert surface.mode == "speak"
    assert events.count(("AudioOutFirst", None)) == 2
    await session.handle_control(snapshot(output_level=0.3, playback_active=True))
    assert events.count(("AudioOutFirst", None)) == 2
    session._response_revisions.clear()
    await session.handle_control(snapshot(input_level=0.66, input_active=True))
    assert surface.mode == "listen"
    assert surface.level == pytest.approx(0.66)


@pytest.mark.asyncio
async def test_expired_and_closed_media_cannot_leave_stuck_levels(presentation):
    session, surface, _, _ = presentation
    await session.handle_control(snapshot(output_level=0.7, playback_active=True))
    session._expire_media_levels()
    await asyncio.gather(*tuple(session._control_tasks))
    assert not session.playback_active
    assert surface.level == 0
    assert surface.mode == "listen"
    session._closing = True
    session._clear_media_levels()
    await session.handle_control(snapshot(input_level=0.9, input_active=True))
    assert surface.level == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"input_level": float("nan")},
        {"output_level": float("inf")},
        {"input_level": -1},
        {"output_level": 1.1},
        {"playback_active": "true"},
        {"input_active": 1},
        {"input_level": True},
        {"unexpected": "ignored"},
    ],
)
async def test_invalid_measurements_cannot_repaint_the_bar(presentation, bad):
    session, surface, frames, _ = presentation
    await session.handle_control({**snapshot(), **bad})
    assert surface.mode == "idle"
    assert not frames
    assert session._media_timeout is None


def test_python_typescript_media_fields_stay_in_parity():
    root = Path(__file__).resolve().parents[2]
    source = (root / "jarvis/ui/web/frontend/src/lib/mediaLevels.ts").read_text(encoding="utf-8")
    fields = re.search(r"interface MediaLevels\s*\{([^}]+)\}", source).group(1)
    assert set(re.findall(r"^\s*(\w+):", fields, re.M)) == set(MediaLevels.model_fields)
    assert 'type: "media_levels"' in fields


@pytest.mark.asyncio
async def test_speech_completion_keeps_other_work_visible(presentation):
    session, surface, _, _ = presentation
    await session.handle_control(snapshot())
    work = asyncio.create_task(asyncio.Event().wait())
    session._jobs.add(work)
    try:
        await session._note_turn_end()
        assert surface.mode == "think"
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        # A completed job may still be in the set until its done callback runs.
        await session.handle_control(snapshot(output_level=0.6, playback_active=True))
        await session.handle_control(snapshot())
        assert surface.mode == "listen"
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.asyncio
async def test_older_loaded_microphone_module_does_not_end_voice(presentation, monkeypatch):
    session, surface, _, _ = presentation
    monkeypatch.delattr(mic_level, "claim_external")
    monkeypatch.delattr(mic_level, "release_external")
    await session.handle_control(snapshot(input_level=0.6, input_active=True))
    assert session.is_active
    assert not session.failed
    await session.handle_control(snapshot(output_level=0.7, playback_active=True))
    assert surface.mode == "speak"
    session._clear_media_levels()
    assert session.is_active


@pytest.mark.asyncio
async def test_meter_sink_failure_is_not_a_session_failure(presentation, monkeypatch):
    session, surface, _, _ = presentation

    def unavailable_meter(owner):
        raise RuntimeError("Synthetic meter failure")

    monkeypatch.setattr(mic_level, "claim_external", unavailable_meter)
    await session.handle_control(snapshot(input_level=0.6, input_active=True))
    assert session.is_active
    assert not session.failed
    await session.handle_control({"type": "playback_state", "active": True})
    assert surface.mode == "speak"
