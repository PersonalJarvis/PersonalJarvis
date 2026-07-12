"""Desktop voice-mode routing and duplex lifecycle regression tests."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.events import MessageSent
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.constants import (
    HANGUP_ERROR,
    HANGUP_HOTKEY,
    HANGUP_SHUTDOWN,
    HANGUP_TURN_COMPLETE,
)
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


class _OneShotVad:
    def __init__(self, captured: list[bytes]) -> None:
        self._captured = captured

    def utterances(self, stream):  # noqa: ANN001, ANN201
        async def _one():
            async for chunk in stream:
                self._captured.append(chunk.pcm)
                yield chunk.pcm
                return

        return _one()


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


class _HandshakeOnlyRealtimeSession(_FakeRealtimeSession):
    def __init__(self, send_binary, send_json) -> None:
        super().__init__(send_binary, send_json)
        self.ready = asyncio.Event()

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
        self.ready.set()


class _CommittedFailureRealtimeSession(_HandshakeOnlyRealtimeSession):
    async def handle_control(self, message) -> None:
        await super().handle_control(message)
        await self._send_json(
            {
                "type": "transcript",
                "role": "user",
                "text": "run the action",
                "is_final": True,
            }
        )

    async def wait_finished(self) -> None:
        return None


def _pipe(mode: str = "realtime") -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._config = SimpleNamespace(
        # model_fields_set mirrors an EXPLICIT user pick (mode in the TOML);
        # the silent-default fallback test overrides it with an empty set.
        voice=SimpleNamespace(mode=mode, model_fields_set={"mode"}),
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
async def test_desktop_cpu_barge_in_cancels_and_forwards_user_preroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    output_started = asyncio.Event()
    forwarded = b"\x09\x00" * 32

    class _Detector:
        def warmup(self) -> None:
            return None

        def start_output(self) -> None:
            return None

        def stop_output(self) -> None:
            return None

        def feed(self, _pcm: bytes) -> bytes:
            return forwarded

    class _Mic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def stream(self):
            await output_started.wait()
            yield AudioChunk(
                pcm=b"\x01\x00" * 32, sample_rate=16_000, timestamp_ns=0
            )
            await asyncio.sleep(0.05)
            yield AudioChunk(
                pcm=b"\x02\x00" * 32, sample_rate=16_000, timestamp_ns=0
            )
            await asyncio.Event().wait()

    class _Session(_HandshakeOnlyRealtimeSession):
        def __init__(self, send_binary, send_json) -> None:
            super().__init__(send_binary, send_json)
            self.audio_frames: list[bytes] = []
            self.forwarded = asyncio.Event()

        async def handle_control(self, message) -> None:
            self.controls.append(message)
            if message.get("type") == "audio_start":
                await self._send_json(
                    {
                        "type": "audio_ready",
                        "provider": "fake-live",
                        "input_sample_rate": 16_000,
                        "output_sample_rate": 24_000,
                    }
                )
                await self._send_binary(b"\x03\x00" * 32)
                output_started.set()
            elif message.get("type") == "barge_in":
                await self._send_json({"type": "tts_cancel"})

        async def handle_audio_frame(self, pcm: bytes) -> None:
            self.audio_frames.append(pcm)
            if pcm == forwarded:
                self.forwarded.set()

        async def wait_finished(self) -> None:
            await self.forwarded.wait()

    built: dict[str, object] = {}

    def _build(**kwargs):
        built.update(kwargs)
        session = _Session(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        "jarvis.realtime.desktop.DesktopRealtimeBargeInDetector", _Detector
    )
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _Mic())

    reason = await asyncio.wait_for(pipe._active_realtime_session(), timeout=2.0)

    session = built["session"]
    assert reason == HANGUP_ERROR
    assert {"type": "barge_in"} in session.controls
    assert session.audio_frames[-1] == forwarded
    assert built["half_duplex"] is True


@pytest.mark.asyncio
async def test_completed_startup_tasks_do_not_end_healthy_realtime_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed build/handshake is not a live-session completion signal."""
    pipe = _pipe()
    built: dict[str, _HandshakeOnlyRealtimeSession] = {}
    built_ready = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _build(**kwargs):
        session = _HandshakeOnlyRealtimeSession(
            kwargs["send_binary"], kwargs["send_json"]
        )
        built["session"] = session
        loop.call_soon_threadsafe(built_ready.set)
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())
    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(built_ready.wait(), timeout=0.5)
    await asyncio.wait_for(built["session"].ready.wait(), timeout=0.5)
    await asyncio.sleep(0)
    assert task.done() is False

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=0.5) == HANGUP_HOTKEY
    assert built["session"].end_reason == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_committed_realtime_turn_is_never_replayed_through_classic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider failure after a final transcript must not duplicate tools."""
    pipe = _pipe()
    built: dict[str, _CommittedFailureRealtimeSession] = {}

    def _build(**kwargs):
        session = _CommittedFailureRealtimeSession(
            kwargs["send_binary"], kwargs["send_json"]
        )
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    buffer = pipeline_mod._SessionInputBuffer(  # noqa: SLF001
        initial=(AudioChunk(pcm=b"command", sample_rate=16_000, timestamp_ns=1),)
    )

    reason = await asyncio.wait_for(
        pipe._active_realtime_session(input_buffer=buffer),
        timeout=1.0,
    )

    assert reason == HANGUP_ERROR
    assert built["session"].end_reason == HANGUP_ERROR


@pytest.mark.asyncio
async def test_realtime_mode_routes_before_the_classic_microphone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    calls = 0

    async def _run_realtime(*, input_buffer=None):  # noqa: ANN001
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

    async def _failed_realtime(*, input_buffer=None):  # noqa: ANN001
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
async def test_default_realtime_mode_falls_back_silently_without_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realtime is the product DEFAULT now: a keyless install must fall back
    to the pipeline without speaking the unavailable notice every call."""
    pipe = _pipe()
    pipe._config.voice.model_fields_set = set()
    notices = 0

    async def _failed_realtime(*, input_buffer=None):  # noqa: ANN001
        return None

    async def _notice():
        nonlocal notices
        notices += 1

    pipe._active_realtime_session = _failed_realtime  # type: ignore[method-assign]
    pipe._speak_realtime_unavailable = _notice  # type: ignore[method-assign]
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic()
    )

    reason = await asyncio.wait_for(pipe._active_session(), timeout=2.0)

    assert reason == HANGUP_SHUTDOWN
    assert notices == 0


@pytest.mark.asyncio
async def test_capture_first_realtime_fallback_is_visible_and_preserves_audio() -> None:
    """Fallback status is visible without speaking into the live microphone."""
    pipe = _pipe()
    opening = AudioChunk(pcm=b"opening", sample_rate=16_000, timestamp_ns=1)
    buffer = pipeline_mod._SessionInputBuffer(initial=(opening,))  # noqa: SLF001
    buffer.finish()
    captured: list[bytes] = []
    events: list[object] = []
    pipe._vad = _OneShotVad(captured)
    pipe._session_end_reason = HANGUP_TURN_COMPLETE

    async def _failed_realtime(*, input_buffer=None):  # noqa: ANN001, ANN202
        return None

    async def _must_not_speak() -> None:
        pytest.fail("capture-first fallback must not speak into its live microphone")

    async def _publish(event) -> None:  # noqa: ANN001
        events.append(event)

    async def _handle(_pcm: bytes) -> bool:
        return False

    pipe._active_realtime_session = _failed_realtime  # type: ignore[method-assign]
    pipe._speak_realtime_unavailable = _must_not_speak  # type: ignore[method-assign]
    pipe._publish_event = _publish  # type: ignore[method-assign]
    pipe._handle_utterance = _handle  # type: ignore[method-assign]

    assert await pipe._active_session(input_buffer=buffer) == HANGUP_TURN_COMPLETE
    assert captured == [b"opening"]
    notices = [event for event in events if isinstance(event, MessageSent)]
    assert len(notices) == 1
    assert notices[0].role == "system"
    assert "classic voice pipeline" in notices[0].text


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


@pytest.mark.asyncio
async def test_pipeline_mode_listens_with_default_thread_pool_exhausted() -> None:
    """Classic VAD startup must not depend on workers used by wake or local STT."""
    pipe = _pipe(mode="pipeline")
    opening_pcm = b"\x00\x40" * 320
    opening = AudioChunk(pcm=opening_pcm, sample_rate=16_000, timestamp_ns=1)
    buffer = pipeline_mod._SessionInputBuffer(initial=(opening,))  # noqa: SLF001
    buffer.finish()
    captured: list[bytes] = []
    pipe._vad = _OneShotVad(captured)
    pipe._session_end_reason = HANGUP_TURN_COMPLETE

    async def _handle(pcm: bytes) -> bool:
        assert pcm == opening_pcm
        return False

    pipe._handle_utterance = _handle  # type: ignore[method-assign]

    loop = asyncio.get_running_loop()
    release_pool = threading.Event()
    blockers = [loop.run_in_executor(None, release_pool.wait) for _ in range(64)]
    await asyncio.sleep(0.1)
    try:
        reason = await asyncio.wait_for(
            pipe._active_session(input_buffer=buffer),
            timeout=1.0,
        )
    finally:
        release_pool.set()
        await asyncio.gather(*blockers, return_exceptions=True)

    assert reason == HANGUP_TURN_COMPLETE
    assert captured == [opening_pcm]


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


@pytest.mark.asyncio
async def test_desktop_loop_returns_voice_pattern_on_session_hangup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A voice hang-up ends the call for real — no classic-pipeline fallback."""

    class _HangupSession(_FakeRealtimeSession):
        hangup_reason = "voice_pattern"

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

        async def wait_finished(self) -> None:
            return None

    pipe = _pipe()
    built: dict[str, object] = {}

    def _build(**kwargs):
        session = _HangupSession(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic()
    )

    reason = await asyncio.wait_for(pipe._active_realtime_session(), timeout=2.0)

    assert reason == "voice_pattern"
    assert built["session"].end_reason == "voice_pattern"


async def _collect_stream(pipe: SpeechPipeline, chunks: list) -> list:
    async def _source():
        for chunk in chunks:
            yield chunk

    return [chunk async for chunk in pipe._session_input_stream(_source())]


@pytest.mark.asyncio
async def test_session_input_stream_feeds_mic_level() -> None:
    from jarvis.audio import mic_level
    from jarvis.audio.capture import AudioChunk

    mic_level.reset_for_tests()
    levels: list[float] = []
    unsubscribe = mic_level.subscribe(levels.append)
    try:
        pipe = _pipe()
        loud = AudioChunk(pcm=b"\x00\x40" * 256, sample_rate=16_000, timestamp_ns=1)
        quiet = AudioChunk(pcm=b"\x10\x00" * 256, sample_rate=16_000, timestamp_ns=2)

        passed = await _collect_stream(pipe, [loud, quiet])

        assert passed == [loud, quiet]
        assert len(levels) == 2
        assert all(0.0 <= level <= 1.0 for level in levels)
        assert levels[0] > 0.0
    finally:
        unsubscribe()
        mic_level.reset_for_tests()


@pytest.mark.asyncio
async def test_session_input_stream_muted_feeds_no_level() -> None:
    from jarvis.audio import mic_level
    from jarvis.audio.capture import AudioChunk

    mic_level.reset_for_tests()
    levels: list[float] = []
    unsubscribe = mic_level.subscribe(levels.append)
    try:
        pipe = _pipe()
        pipe._muted = True
        chunk = AudioChunk(pcm=b"\x00\x40" * 256, sample_rate=16_000, timestamp_ns=1)

        passed = await _collect_stream(pipe, [chunk])

        assert passed == []
        assert levels == []
    finally:
        unsubscribe()
        mic_level.reset_for_tests()


@pytest.mark.asyncio
async def test_shared_input_keeps_meter_live_while_realtime_build_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider/tool assembly must not starve the capture or Jarvis Bar."""
    from jarvis.audio import mic_level
    from jarvis.audio.capture import AudioChunk

    pipe = _pipe()
    build_entered = threading.Event()
    release_build = threading.Event()

    def _build(**kwargs):
        build_entered.set()
        assert release_build.wait(timeout=1.0)
        return _FakeRealtimeSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    buffer = pipeline_mod._SessionInputBuffer()  # noqa: SLF001
    mic_level.reset_for_tests()
    levels: list[float] = []
    unsubscribe = mic_level.subscribe(levels.append)
    task = asyncio.create_task(pipe._active_realtime_session(input_buffer=buffer))
    try:
        for _ in range(100):
            if build_entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert build_entered.is_set()

        buffer.put(
            AudioChunk(
                pcm=b"\x00\x40" * 320,
                sample_rate=16_000,
                timestamp_ns=1,
            )
        )
        await asyncio.sleep(0)
        assert levels and levels[-1] > 0.0
    finally:
        release_build.set()

    assert await asyncio.wait_for(task, timeout=1.0) == HANGUP_TURN_COMPLETE
    unsubscribe()
    mic_level.reset_for_tests()


@pytest.mark.asyncio
async def test_realtime_builder_survives_exhausted_default_thread_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wake/STT workers must never queue the realtime control path behind them."""
    pipe = _pipe()
    loop = asyncio.get_running_loop()
    release_pool = threading.Event()
    blockers = [loop.run_in_executor(None, release_pool.wait) for _ in range(64)]
    await asyncio.sleep(0.1)

    build_called = threading.Event()

    def _build(**kwargs):
        build_called.set()
        return _FakeRealtimeSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    buffer = pipeline_mod._SessionInputBuffer()  # noqa: SLF001
    try:
        reason = await asyncio.wait_for(
            pipe._active_realtime_session(input_buffer=buffer),
            timeout=1.0,
        )
    finally:
        release_pool.set()
        await asyncio.gather(*blockers, return_exceptions=True)

    assert reason == HANGUP_TURN_COMPLETE
    assert build_called.is_set()


@pytest.mark.asyncio
async def test_hangup_cancels_realtime_start_while_builder_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    build_entered = threading.Event()
    release_build = threading.Event()

    def _build(**kwargs):
        build_entered.set()
        release_build.wait(timeout=1.0)
        return _FakeRealtimeSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    buffer = pipeline_mod._SessionInputBuffer()  # noqa: SLF001
    task = asyncio.create_task(pipe._active_realtime_session(input_buffer=buffer))
    for _ in range(100):
        if build_entered.is_set():
            break
        await asyncio.sleep(0.001)
    assert build_entered.is_set()

    pipe._hangup_event.set()
    try:
        assert await asyncio.wait_for(task, timeout=0.5) == HANGUP_HOTKEY
    finally:
        release_build.set()
