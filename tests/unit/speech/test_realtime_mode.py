"""Desktop voice-mode routing and duplex lifecycle regression tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.sessions.constants import HANGUP_SHUTDOWN, HANGUP_TURN_COMPLETE
from jarvis.speech.pipeline import SpeechPipeline


class _FakePlayer:
    def __init__(self) -> None:
        self.pcm: list[bytes] = []
        self.stopped = 0

    async def play_chunks(self, chunks) -> None:
        async for chunk in chunks:
            self.pcm.append(chunk.pcm)

    def stop(self) -> None:
        self.stopped += 1


class _SilentMic:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def stream(self):
        await asyncio.Event().wait()
        if False:  # pragma: no cover - makes this an async iterator
            yield None


class _EmptyVad:
    def utterances(self, _stream):
        async def _empty():
            if False:  # pragma: no cover - makes this an async iterator
                yield b""

        return _empty()


class _FakeRealtimeSession:
    def __init__(self, send_binary, send_json) -> None:
        self._send_binary = send_binary
        self._send_json = send_json
        self.controls: list[dict[str, object]] = []
        self.end_reason = ""
        self._forever = asyncio.Event()

    async def handle_control(self, message) -> None:
        self.controls.append(message)
        await self._send_json(
            {
                "type": "audio_ready",
                "provider": "fake-live",
                "input_sample_rate": 16_000,
                "output_sample_rate": 24_000,
            }
        )
        await self._send_json(
            {
                "type": "transcript",
                "role": "user",
                "text": "hello",
                "is_final": True,
            }
        )
        await self._send_binary(b"\x01\x00" * 16)
        await self._send_json({"type": "turn_complete"})

    async def handle_audio_frame(self, _pcm: bytes) -> None:
        return None

    async def wait_finished(self) -> None:
        await self._forever.wait()

    async def end(self, *, reason: str = "") -> None:
        self.end_reason = reason


def _pipe(mode: str = "realtime") -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._config = SimpleNamespace(
        voice=SimpleNamespace(mode=mode),
        brain=SimpleNamespace(reply_language="en"),
    )
    pipe._player = _FakePlayer()
    pipe._bus = None
    pipe._input_device = None
    pipe._input_priority = ()
    pipe._hangup_event = asyncio.Event()
    pipe._continue_listening_after_response = False
    pipe._current_voice_session_id = "desktop-session"
    pipe._active_voice_mode = mode
    pipe._active_realtime_provider = ""
    pipe._active_realtime_model = ""
    pipe._voice_engine_transitioning = False
    pipe._reopen_after_engine_change = False
    pipe._engine_change_reason = ""
    pipe._state = pipeline_mod.PipelineState.IDLE
    pipe._muted = False
    pipe._input_suppressed_until_ns = 0
    pipe._ptt_mode = False
    pipe._vad = _EmptyVad()
    pipe._idle_timeout_s = 0.1
    pipe._idle_hangup_enabled = True
    pipe._session_end_reason = None
    pipe._carry_pcm = bytearray()
    pipe._carry_started_monotonic = None
    pipe._last_endpoint_reason = None
    pipe._last_announcement_spoken_monotonic = None
    pipe._last_answer_floor_monotonic = None
    states = []

    async def _set_state(state) -> None:
        states.append(state)

    async def _publish(_event) -> None:
        return None

    pipe._set_turn_state = _set_state  # type: ignore[method-assign]
    pipe._publish_event = _publish  # type: ignore[method-assign]
    pipe._test_states = states  # type: ignore[attr-defined]
    return pipe


@pytest.mark.asyncio
async def test_desktop_realtime_handshake_streams_audio_and_ends_single_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    built: dict[str, object] = {}

    def _build(**kwargs):
        built.update(kwargs)
        session = _FakeRealtimeSession(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    reason = await asyncio.wait_for(pipe._active_realtime_session(), timeout=2.0)

    assert reason == HANGUP_TURN_COMPLETE
    assert built["half_duplex"] is True
    assert built["surface"] == "desktop"
    assert built["session_id"] == "desktop-session"
    assert pipe._player.pcm == [b"\x01\x00" * 16]
    assert built["session"].end_reason == HANGUP_TURN_COMPLETE


@pytest.mark.asyncio
async def test_realtime_mode_routes_before_the_classic_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    calls = 0

    async def _run_realtime():
        nonlocal calls
        calls += 1
        return HANGUP_TURN_COMPLETE

    pipe._active_realtime_session = _run_realtime  # type: ignore[method-assign]

    def _classic_mic_must_not_open(**_kwargs):
        raise AssertionError("classic microphone opened during a healthy realtime session")

    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _classic_mic_must_not_open)

    assert await pipe._active_session() == HANGUP_TURN_COMPLETE
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_realtime_session_explains_and_falls_back_in_same_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    notices = 0

    async def _failed_realtime():
        return None

    async def _notice():
        nonlocal notices
        notices += 1

    pipe._active_realtime_session = _failed_realtime  # type: ignore[method-assign]
    pipe._speak_realtime_unavailable = _notice  # type: ignore[method-assign]
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    reason = await asyncio.wait_for(pipe._active_session(), timeout=2.0)

    assert reason == HANGUP_SHUTDOWN
    assert notices == 1


@pytest.mark.asyncio
async def test_pipeline_mode_never_enters_realtime_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe(mode="pipeline")

    async def _must_not_run():
        raise AssertionError("realtime branch ran while pipeline mode was selected")

    pipe._active_realtime_session = _must_not_run  # type: ignore[method-assign]
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    assert await asyncio.wait_for(pipe._active_session(), timeout=2.0) == HANGUP_SHUTDOWN


def test_live_mode_change_schedules_active_call_for_reopen() -> None:
    pipe = _pipe(mode="pipeline")
    pipe._state = pipeline_mod.PipelineState.ACTIVE
    pipe._active_voice_mode = "pipeline"
    hangups: list[bool] = []
    pipe._trigger_voice_hangup = lambda: hangups.append(True)  # type: ignore[method-assign]

    restarted = pipe.apply_voice_mode("realtime")

    assert restarted is True
    assert pipe._config.voice.mode == "realtime"
    assert pipe._reopen_after_engine_change is True
    assert pipe._voice_engine_transitioning is True
    assert hangups == [True]


def test_runtime_status_never_confuses_configured_and_effective_mode() -> None:
    pipe = _pipe(mode="realtime")
    pipe._state = pipeline_mod.PipelineState.ACTIVE
    pipe._active_voice_mode = "pipeline"
    pipe._current_voice_session_id = "classic-call"

    status = pipe.voice_engine_status()

    assert status["configured_mode"] == "realtime"
    assert status["active_session_mode"] == "pipeline"
    assert status["session_active"] is True
    assert status["active_session_provider"] == ""
