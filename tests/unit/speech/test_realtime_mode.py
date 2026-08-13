"""Desktop voice-mode routing and duplex lifecycle regression tests."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.events import MessageSent, SpeechSpoken
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
        self.output_latency_s = 0.0

    async def play_chunks(self, chunks, *, should_play=None) -> None:
        async for chunk in chunks:
            if should_play is not None and not should_play():
                return
            self.pcm.append(chunk.pcm)

    def stop(self) -> None:
        self.stopped += 1


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _FakeTTS:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.calls: list[tuple[str, str | None]] = []

    def synthesize(self, text: str, *, language_code: str | None = None):
        self.calls.append((text, language_code))

        async def _chunks():
            yield AudioChunk(pcm=self.pcm, sample_rate=24_000, timestamp_ns=0)

        return _chunks()


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
async def test_interim_audio_returns_to_thinking_before_final_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    bridge_pcm = b"\x01\x00" * 16
    final_pcm = b"\x02\x00" * 16

    class _InterimSession(_FakeRealtimeSession):
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
                    "text": "check my calendar",
                    "is_final": True,
                }
            )
            await self._send_binary(bridge_pcm)
            await self._send_json({"type": "thinking"})
            await self._send_binary(final_pcm)
            await self._send_json({"type": "turn_complete"})

    def _build(**kwargs):
        return _InterimSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        pipeline_mod,
        "MicrophoneCapture",
        lambda **_kwargs: _SilentMic(),
    )

    reason = await asyncio.wait_for(pipe._active_realtime_session(), timeout=2.0)

    assert reason == HANGUP_TURN_COMPLETE
    assert pipe._player.pcm == [bridge_pcm, final_pcm]
    state_changes = [
        state
        for index, state in enumerate(pipe._test_states)
        if index == 0 or state != pipe._test_states[index - 1]
    ]
    assert state_changes == [
        pipeline_mod.TurnTakingState.PROCESSING,
        pipeline_mod.TurnTakingState.JARVIS_SPEAKING,
        pipeline_mod.TurnTakingState.PROCESSING,
        pipeline_mod.TurnTakingState.JARVIS_SPEAKING,
        pipeline_mod.TurnTakingState.LISTENING,
    ]


@pytest.mark.asyncio
async def test_unsafe_output_cancel_stops_playback_and_returns_to_listening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    fallback_pcm = b"\x06\x00" * 32
    # Strict mode separation: the audible emergency phrase comes from the
    # REALTIME-scoped TTS; the pipeline [tts] instance must stay untouched.
    pipe._tts = _FakeTTS(b"\x0f\x00" * 32)
    surface_tts = _FakeTTS(fallback_pcm)
    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts",
        lambda _cfg, _provider: surface_tts,
    )
    cancel_delivered = asyncio.Event()
    built: dict[str, object] = {}

    class _UnsafeOutputSession(_HandshakeOnlyRealtimeSession):
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
            await self._send_binary(b"\x05\x00" * 32)
            await self._send_json({"type": "tts_cancel"})
            await self._send_json({"type": "error_spoken", "text": "An error occurred."})
            cancel_delivered.set()

    def _build(**kwargs):
        session = _UnsafeOutputSession(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    task = asyncio.create_task(pipe._active_realtime_session())
    # Session construction lazily imports the realtime stack and can exceed a
    # 0.5 s wall-clock budget when this test follows other audio tests on the
    # slower macOS CI runner. The event itself remains the deterministic gate.
    await asyncio.wait_for(cancel_delivered.wait(), timeout=2.0)
    await asyncio.sleep(0)

    assert pipe._player.stopped >= 1
    assert fallback_pcm in pipe._player.pcm
    assert surface_tts.calls == [("An error occurred.", "en-US")]
    assert pipe._tts.calls == []
    # Collapsed to real transitions rather than a tail window. This fake
    # scripts the whole turn INSIDE handle_control, so the pipeline's
    # once-per-session post-handshake LISTENING lands AFTER the surface render
    # instead of before it — live, the provider pump delivers these frames long
    # after the handshake returned. A tail window therefore saw
    # [LISTENING, LISTENING] and read as "the speaking state is gone", while
    # the emergency phrase had in fact played (asserted above). Collapsing
    # duplicates pins all four genuine transitions and holds under either
    # scheduling order; the sibling interim-audio test does the same.
    transitions = [
        state
        for index, state in enumerate(pipe._test_states)
        if index == 0 or state != pipe._test_states[index - 1]
    ]
    assert transitions == [
        pipeline_mod.TurnTakingState.JARVIS_SPEAKING,
        pipeline_mod.TurnTakingState.LISTENING,
        pipeline_mod.TurnTakingState.JARVIS_SPEAKING,
        pipeline_mod.TurnTakingState.LISTENING,
    ]

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=1.0) == HANGUP_HOTKEY
    assert built["session"].end_reason == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_error_spoken_renders_through_realtime_scoped_surface_tts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode separation (2026-07-17): the realtime emergency re-render resolves
    a REALTIME-scoped surface TTS for the active provider instead of blindly
    speaking through the pipeline's separately configured [tts] instance, and
    the spoken-voice label is read from the instance that actually spoke."""
    pipe = _pipe()
    pipeline_pcm = b"\x07\x00" * 32
    surface_pcm = b"\x08\x00" * 32
    pipeline_tts = _FakeTTS(pipeline_pcm)
    surface_tts = _FakeTTS(surface_pcm)
    pipe._tts = pipeline_tts
    pipe._bus = _RecordingBus()
    resolved: dict[str, object] = {}
    spoken_delivered = asyncio.Event()

    def _scoped_surface_tts(cfg, provider):
        resolved["cfg"] = cfg
        resolved["provider"] = provider
        return surface_tts

    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts", _scoped_surface_tts
    )

    class _ErrorSpokenSession(_HandshakeOnlyRealtimeSession):
        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            await self._send_json(
                {
                    "type": "error_spoken",
                    "text": "The grounded reply.",
                    "language": "en",
                    "spoken_kind": "withheld",
                    "detail": "response identity mismatch",
                }
            )
            spoken_delivered.set()

    def _build(**kwargs):
        return _ErrorSpokenSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(spoken_delivered.wait(), timeout=0.5)
    await asyncio.sleep(0)

    # The resolver received the active realtime provider, and the scoped
    # instance did ALL the speaking — the pipeline [tts] stayed untouched.
    assert resolved["provider"] == "fake-live"
    assert [text for text, _lang in surface_tts.calls] == ["The grounded reply."]
    assert pipeline_tts.calls == []
    assert surface_pcm in pipe._player.pcm
    await asyncio.sleep(0)
    spoken = [event for event in pipe._bus.events if isinstance(event, SpeechSpoken)]
    assert len(spoken) == 1
    assert spoken[0].spoken_kind == "withheld"
    assert spoken[0].detail == "response identity mismatch"

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=0.5) == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_error_spoken_while_muted_is_held_and_delivered_on_unmute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grounded surface reply that lands while the voice is muted must not
    be silently discarded (live 2026-07-21 17:45: the orb mute gesture landed
    0.5 s before the surface fallback and the whole answer was never heard).
    Mute means quiet NOW — the reply is held and spoken the moment the user
    unmutes, within the same session."""
    pipe = _pipe()
    surface_pcm = b"\x09\x00" * 32
    surface_tts = _FakeTTS(surface_pcm)
    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts",
        lambda _cfg, _provider: surface_tts,
    )
    spoken_delivered = asyncio.Event()

    class _ErrorSpokenSession(_HandshakeOnlyRealtimeSession):
        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            await self._send_json(
                {
                    "type": "error_spoken",
                    "text": "Tomorrow is Wednesday.",
                    "language": "en",
                }
            )
            spoken_delivered.set()

    def _build(**kwargs):
        return _ErrorSpokenSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    pipe._muted = True
    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(spoken_delivered.wait(), timeout=2.0)
    await asyncio.sleep(0.05)

    # Muted: nothing audible yet — but the reply is HELD, not dropped.
    assert surface_tts.calls == []
    assert pipe._player.pcm == []

    pipe._muted = False

    async def _wait_for_spoken() -> None:
        while not surface_tts.calls:  # noqa: ASYNC110 - the fake TTS has no event hook to await
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_wait_for_spoken(), timeout=2.0)
    await asyncio.sleep(0.05)

    assert [text for text, _lang in surface_tts.calls] == [
        "Tomorrow is Wednesday."
    ]
    assert surface_pcm in pipe._player.pcm

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=1.0) == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_held_muted_reply_stays_text_only_when_session_ends_muted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ending the session while still muted keeps the held reply text-only
    (the transcript carries it) and tears down cleanly — no late audio, no
    leaked watcher task."""
    pipe = _pipe()
    surface_tts = _FakeTTS(b"\x0a\x00" * 32)
    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts",
        lambda _cfg, _provider: surface_tts,
    )
    spoken_delivered = asyncio.Event()

    class _ErrorSpokenSession(_HandshakeOnlyRealtimeSession):
        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            await self._send_json(
                {
                    "type": "error_spoken",
                    "text": "The grounded reply.",
                    "language": "en",
                }
            )
            spoken_delivered.set()

    def _build(**kwargs):
        return _ErrorSpokenSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    pipe._muted = True
    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(spoken_delivered.wait(), timeout=2.0)
    await asyncio.sleep(0.05)

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=1.0) == HANGUP_HOTKEY
    assert surface_tts.calls == []
    assert pipe._player.pcm == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_before_first_chunk",
    [True, False],
    ids=("during-synthesis", "between-chunks"),
)
async def test_tts_cancel_stops_surface_generator_before_it_can_reopen_output(
    monkeypatch: pytest.MonkeyPatch,
    cancel_before_first_chunk: bool,
) -> None:
    """A local barge-in owns both PortAudio and the surface-TTS producer.

    Stopping only the shared player left the async producer alive. Its next
    chunk reopened PortAudio after the turn had returned to LISTENING, creating
    untracked zombie speech and blocking the realtime callback behind it.
    """

    pipe = _pipe()
    first_pcm = b"\x11\x00" * 32
    zombie_pcm = b"\x22\x00" * 32
    synthesis_started = asyncio.Event()
    first_chunk_consumed = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    generator_closed = asyncio.Event()
    cancel_delivered = asyncio.Event()
    spoken_receipts: list[str] = []
    pipe._emit_spoken = (  # type: ignore[method-assign]
        lambda text, *_args, **_kwargs: spoken_receipts.append(text)
    )

    class _TwoChunkSurfaceTTS:
        def synthesize(
            self,
            _text: str,
            *,
            language_code: str | None = None,
        ):
            del language_code

            async def _chunks():
                try:
                    synthesis_started.set()
                    if cancel_before_first_chunk:
                        await release_first.wait()
                    yield AudioChunk(
                        pcm=first_pcm,
                        sample_rate=24_000,
                        timestamp_ns=0,
                    )
                    first_chunk_consumed.set()
                    await release_second.wait()
                    yield AudioChunk(
                        pcm=zombie_pcm,
                        sample_rate=24_000,
                        timestamp_ns=0,
                    )
                finally:
                    generator_closed.set()

            return _chunks()

    surface_tts = _TwoChunkSurfaceTTS()
    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts",
        lambda _cfg, _provider: surface_tts,
    )

    class _SurfaceCancelSession(_HandshakeOnlyRealtimeSession):
        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            surface_callback = asyncio.create_task(
                self._send_json(
                    {
                        "type": "error_spoken",
                        "text": "A grounded answer with two audio chunks.",
                        "language": "en",
                    }
                )
            )
            if cancel_before_first_chunk:
                await synthesis_started.wait()
            else:
                await first_chunk_consumed.wait()

            await self._send_json({"type": "tts_cancel"})
            release_first.set()
            release_second.set()
            await asyncio.wait_for(surface_callback, timeout=0.5)
            cancel_delivered.set()

    def _build(**kwargs):
        return _SurfaceCancelSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        pipeline_mod,
        "MicrophoneCapture",
        lambda **_kwargs: _SilentMic(),
    )

    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(cancel_delivered.wait(), timeout=0.5)
    await asyncio.sleep(0)

    assert (first_pcm in pipe._player.pcm) is not cancel_before_first_chunk
    assert zombie_pcm not in pipe._player.pcm
    assert generator_closed.is_set()
    assert spoken_receipts == []
    assert pipe._player.stopped >= 1
    assert not task.done(), "surface cancellation must not end the live call"

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=0.5) == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_tts_cancel_invalidates_surface_render_before_task_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel during the speaking-state callback prevents late synthesis."""

    pipe = _pipe()
    surface_tts = _FakeTTS(b"\x33\x00" * 32)
    speaking_state_entered = asyncio.Event()
    release_speaking_state = asyncio.Event()
    cancel_delivered = asyncio.Event()

    async def _set_state(state) -> None:
        pipe._test_states.append(state)
        if state == pipeline_mod.TurnTakingState.JARVIS_SPEAKING:
            speaking_state_entered.set()
            await release_speaking_state.wait()

    pipe._set_turn_state = _set_state  # type: ignore[method-assign]
    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts",
        lambda _cfg, _provider: surface_tts,
    )

    class _PrePlaybackCancelSession(_HandshakeOnlyRealtimeSession):
        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            surface_callback = asyncio.create_task(
                self._send_json(
                    {
                        "type": "error_spoken",
                        "text": "This render must never start.",
                        "language": "en",
                    }
                )
            )
            await speaking_state_entered.wait()
            await self._send_json({"type": "tts_cancel"})
            release_speaking_state.set()
            await asyncio.wait_for(surface_callback, timeout=0.5)
            cancel_delivered.set()

    def _build(**kwargs):
        return _PrePlaybackCancelSession(
            kwargs["send_binary"], kwargs["send_json"]
        )

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        pipeline_mod,
        "MicrophoneCapture",
        lambda **_kwargs: _SilentMic(),
    )

    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(cancel_delivered.wait(), timeout=0.5)

    assert surface_tts.calls == []
    assert pipe._player.pcm == []
    assert pipe._test_states[-1] == pipeline_mod.TurnTakingState.LISTENING
    assert not task.done(), "pre-playback cancellation must not end the live call"

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=0.5) == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_error_spoken_without_realtime_scoped_tts_never_borrows_pipeline_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STRICT mode separation (maintainer mandate 2026-07-17): a realtime
    provider without a same-family TTS sibling keeps the emergency re-render
    TEXT-ONLY. The pipeline's [tts] instance is never a last resort — its
    credentials belong to a different, independent mode."""
    pipe = _pipe()
    pipeline_tts = _FakeTTS(b"\x07\x00" * 32)
    pipe._tts = pipeline_tts
    spoken_delivered = asyncio.Event()

    class _ErrorSpokenSession(_HandshakeOnlyRealtimeSession):
        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            await self._send_json(
                {
                    "type": "error_spoken",
                    "text": "The grounded reply.",
                    "language": "en",
                }
            )
            spoken_delivered.set()

    def _build(**kwargs):
        return _ErrorSpokenSession(kwargs["send_binary"], kwargs["send_json"])

    # No build_realtime_surface_tts mock: the REAL resolver runs and finds no
    # TTS sibling for the unmapped "fake-live" family.
    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(spoken_delivered.wait(), timeout=0.5)
    await asyncio.sleep(0)

    assert pipeline_tts.calls == []
    assert pipe._player.pcm == []
    # No JARVIS_SPEAKING flash for an unspoken turn.
    assert pipeline_mod.TurnTakingState.JARVIS_SPEAKING not in pipe._test_states

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=0.5) == HANGUP_HOTKEY


class _VoiceAwareTTS(_FakeTTS):
    """A surface TTS that supports per-utterance voice pinning + listing."""

    def __init__(self, pcm: bytes, voices: list[str]) -> None:
        super().__init__(pcm)
        self.voices = voices
        self.voice_calls: list[str | None] = []

    def list_voices(self, language: str | None = None) -> list[str]:
        return list(self.voices)

    def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
        language_code: str | None = None,
    ):
        self.voice_calls.append(voice)
        return super().synthesize(text, language_code=language_code)


@pytest.mark.parametrize(
    ("catalogue", "expected_voice"),
    [
        (["Fenrir", "Charon"], "Fenrir"),  # hint offered -> voice continuity
        (["leo"], None),  # foreign catalogue -> provider's own default
    ],
)
@pytest.mark.asyncio
async def test_error_spoken_voice_hint_is_capability_gated(
    monkeypatch: pytest.MonkeyPatch,
    catalogue: list[str],
    expected_voice: str | None,
) -> None:
    """Voice-identity continuity (live forensic 2026-07-17 10:04: Fenrir's
    aborted readback re-spoken by Charon): the realtime session's voice hint is
    honored when the resolved surface TTS offers that voice, and silently
    dropped when it does not — a capability check, never a provider pin."""
    pipe = _pipe()
    pipe._tts = _FakeTTS(b"\x07\x00" * 32)
    surface_tts = _VoiceAwareTTS(b"\x08\x00" * 32, catalogue)
    spoken_delivered = asyncio.Event()

    monkeypatch.setattr(
        "jarvis.plugins.tts.build_realtime_surface_tts",
        lambda _cfg, _provider: surface_tts,
    )

    class _VoiceHintSession(_HandshakeOnlyRealtimeSession):
        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            await self._send_json(
                {
                    "type": "error_spoken",
                    "text": "The grounded reply.",
                    "language": "en",
                    "voice": "Fenrir",
                }
            )
            spoken_delivered.set()

    def _build(**kwargs):
        return _VoiceHintSession(kwargs["send_binary"], kwargs["send_json"])

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _SilentMic())

    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(spoken_delivered.wait(), timeout=0.5)
    await asyncio.sleep(0)

    assert surface_tts.voice_calls == [expected_voice]
    assert [text for text, _lang in surface_tts.calls] == ["The grounded reply."]

    pipe._hangup_event.set()
    assert await asyncio.wait_for(task, timeout=0.5) == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_desktop_cpu_barge_in_cancels_and_forwards_user_preroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    output_started = asyncio.Event()
    forwarded = b"\x09\x00" * 32
    # Per INSTANCE: the session builds two detectors — one for playback and one
    # for the silent thinking wait — and they are configured differently on
    # purpose (see the output_active assertions below).
    detector_kwargs: list[dict[str, object]] = []

    class _Detector:
        def __init__(self, **kwargs: object) -> None:
            detector_kwargs.append(dict(kwargs))

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
    # The PLAYBACK detector keeps the probe: it must judge frames against what
    # the speakers are emitting, or its echo gates cannot tell our own voice
    # from the user's.
    assert (
        detector_kwargs[0]["output_active"]
        is pipeline_mod.level_tap.playback_active
    )
    # The THINKING detector must NOT have one. With the probe attached, feed()
    # returns None for as long as nothing is playing, which is exactly the
    # silence it exists to listen through.
    assert any(kwargs.get("output_active") is None for kwargs in detector_kwargs[1:]), (
        "no detector is armed for the silent thinking wait"
    )


@pytest.mark.asyncio
async def test_classic_barge_grace_uses_physical_playback_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe(mode="pipeline")
    detector_kwargs: dict[str, object] = {}

    class _Detector:
        def __init__(self, **kwargs: object) -> None:
            detector_kwargs.update(kwargs)

        def warmup(self) -> None:
            return None

        def start_output(self) -> None:
            return None

        def feed(self, _pcm: bytes) -> bytes:
            return b"confirmed"

    class _Mic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def stream(self):
            yield AudioChunk(
                pcm=b"\x01\x00" * 32,
                sample_rate=16_000,
                timestamp_ns=0,
            )

    monkeypatch.setattr(
        "jarvis.realtime.desktop.DesktopRealtimeBargeInDetector", _Detector
    )
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _Mic())

    assert await pipe._barge_monitor() is True
    assert detector_kwargs["output_active"] is pipeline_mod.level_tap.playback_active


@pytest.mark.asyncio
async def test_post_output_echo_tail_stays_local_and_preserves_immediate_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hardware playback tail must not become a phantom realtime turn."""
    pipe = _pipe()
    pipe._continue_listening_after_response = True
    # The old fixed 10 ms guard has expired before the microphone yields at
    # 50 ms. The active device's 150 ms PortAudio latency must keep both the
    # echo and a confirmed immediate user interruption local until then.
    monkeypatch.setattr(pipeline_mod, "_REALTIME_POST_OUTPUT_ECHO_GUARD_S", 0.01)
    pipe._player.output_latency_s = 0.15
    output_finished = asyncio.Event()
    echo_pcm = b"\x01\x00" * 32
    user_pcm = b"\x02\x00" * 32
    forwarded = b"\x09\x00" * 32
    detector_inputs: list[bytes] = []

    class _Detector:
        def __init__(self, **_kwargs: object) -> None:
            self.active = False

        def warmup(self) -> None:
            return None

        def start_output(self) -> None:
            self.active = True

        def stop_output(self) -> None:
            self.active = False

        def feed(self, pcm: bytes) -> bytes | None:
            detector_inputs.append(pcm)
            if pcm == user_pcm:
                self.active = False
                return forwarded
            return None

    class _Mic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def stream(self):
            await output_finished.wait()
            # The capture task starts before the provider handshake. Let the
            # completed handshake open its provider_ready gate before yielding.
            await asyncio.sleep(0.05)
            yield AudioChunk(pcm=echo_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.sleep(0)
            yield AudioChunk(pcm=user_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.Event().wait()

    class _Session(_HandshakeOnlyRealtimeSession):
        def __init__(self, send_binary, send_json) -> None:
            super().__init__(send_binary, send_json)
            self.audio_frames: list[bytes] = []
            self.received = asyncio.Event()

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
                await self._send_json(
                    {
                        "type": "transcript",
                        "role": "user",
                        "text": "simple question",
                        "is_final": True,
                    }
                )
                await self._send_binary(b"\x03\x00" * 32)
                await self._send_json({"type": "turn_complete"})
                output_finished.set()
            elif message.get("type") == "barge_in":
                await self._send_json({"type": "tts_cancel"})

        async def handle_audio_frame(self, pcm: bytes) -> None:
            self.audio_frames.append(pcm)
            self.received.set()

        async def wait_finished(self) -> None:
            await self.received.wait()

    built: dict[str, object] = {}

    def _build(**kwargs):
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
    assert detector_inputs == [echo_pcm, user_pcm]
    assert session.audio_frames == [forwarded]
    assert {"type": "barge_in"} in session.controls


@pytest.mark.asyncio
async def test_post_output_tail_speech_is_buffered_and_flushed_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quiet user speech right after a turn must reach the provider late, not never.

    The post-drain echo tail used to drop every microphone frame the local
    barge detector did not confirm — a user answering within ~1-2 s of the
    assistant finishing lost their utterance onset entirely. Frames captured
    past the physically-audible phase are now buffered and forwarded when the
    tail expires.
    """
    pipe = _pipe()
    pipe._continue_listening_after_response = True
    monkeypatch.setattr(pipeline_mod, "_REALTIME_POST_OUTPUT_ECHO_GUARD_S", 0.7)
    monkeypatch.setattr(pipeline_mod, "_REALTIME_HW_ECHO_TAIL_MARGIN_S", 0.2)
    output_finished = asyncio.Event()
    echo_pcm = b"\x01\x00" * 32  # audible phase: genuine echo territory
    user_pcm = b"\x02\x00" * 32  # post-audible tail: quiet user speech
    live_pcm = b"\x04\x00" * 32  # after tail expiry: normal pass-through
    detector_inputs: list[bytes] = []

    class _Detector:
        def __init__(self, **_kwargs: object) -> None:
            self.active = False

        def warmup(self) -> None:
            return None

        def start_output(self) -> None:
            self.active = True

        def stop_output(self) -> None:
            self.active = False

        def feed(self, pcm: bytes) -> bytes | None:
            # Never confirms a barge-in: the user's answer is too quiet for
            # the strict local confirm — the exact deaf-window scenario.
            detector_inputs.append(pcm)
            return None

    class _Mic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def stream(self):
            await output_finished.wait()
            await asyncio.sleep(0.05)
            yield AudioChunk(pcm=echo_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.sleep(0.3)
            yield AudioChunk(pcm=user_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.sleep(0.45)
            yield AudioChunk(pcm=live_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.Event().wait()

    class _Session(_HandshakeOnlyRealtimeSession):
        def __init__(self, send_binary, send_json) -> None:
            super().__init__(send_binary, send_json)
            self.audio_frames: list[bytes] = []
            self.received_live = asyncio.Event()

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
                await self._send_json(
                    {
                        "type": "transcript",
                        "role": "user",
                        "text": "simple question",
                        "is_final": True,
                    }
                )
                await self._send_binary(b"\x03\x00" * 32)
                await self._send_json({"type": "turn_complete"})
                output_finished.set()

        async def handle_audio_frame(self, pcm: bytes) -> None:
            self.audio_frames.append(pcm)
            if pcm == live_pcm:
                self.received_live.set()

        async def wait_finished(self) -> None:
            await self.received_live.wait()

    built: dict[str, object] = {}

    def _build(**kwargs):
        session = _Session(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        "jarvis.realtime.desktop.DesktopRealtimeBargeInDetector", _Detector
    )
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _Mic())

    reason = await asyncio.wait_for(pipe._active_realtime_session(), timeout=4.0)

    session = built["session"]
    assert reason == HANGUP_ERROR
    # The audible-phase frame stayed local; the tail frame was inspected but
    # NOT dropped: it reaches the provider ahead of the first live frame.
    assert detector_inputs == [echo_pcm, user_pcm]
    assert session.audio_frames == [user_pcm, live_pcm]
    assert {"type": "barge_in"} not in session.controls


@pytest.mark.asyncio
async def test_terminal_teardown_rejects_audio_from_late_provider_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider unwinding after hotkey cannot resurrect the output stream."""

    pipe = _pipe()
    pipe._continue_listening_after_response = True
    initial_pcm = b"\x01\x00" * 32
    late_pcm = b"\x09\x00" * 32
    output_sent = asyncio.Event()
    built: dict[str, object] = {}

    class _LateOnEndSession(_HandshakeOnlyRealtimeSession):
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
            await self._send_binary(initial_pcm)
            output_sent.set()

        async def end(self, *, reason: str = "") -> None:
            self.end_reason = reason
            # Simulates a callback already queued by the provider while the
            # terminal hotkey boundary is quiescing its receive pump.
            await self._send_binary(late_pcm)

    def _build(**kwargs):
        session = _LateOnEndSession(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        pipeline_mod,
        "MicrophoneCapture",
        lambda **_kwargs: _SilentMic(),
    )

    task = asyncio.create_task(pipe._active_realtime_session())
    await asyncio.wait_for(output_sent.wait(), timeout=1.0)
    await asyncio.sleep(0)
    pipe._hangup_event.set()

    assert await asyncio.wait_for(task, timeout=1.0) == HANGUP_HOTKEY
    assert initial_pcm in pipe._player.pcm
    assert late_pcm not in pipe._player.pcm
    assert built["session"].end_reason == HANGUP_HOTKEY


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


@pytest.mark.asyncio
async def test_capture_latency_extends_the_audible_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A microphone frame handed over at time T was RECORDED earlier.

    Ignoring that, the guard released frames that still carried the
    assistant's own voice: the provider heard itself and the model answered
    its own last sentence. With a slow capture path (MME reports ~0.3 s here)
    the same frame that is safe on a fast one is still echo, and must stay
    local - late, quiet, or not at all, but never uploaded.
    """
    pipe = _pipe()
    pipe._continue_listening_after_response = True
    monkeypatch.setattr(pipeline_mod, "_REALTIME_POST_OUTPUT_ECHO_GUARD_S", 0.7)
    monkeypatch.setattr(pipeline_mod, "_REALTIME_HW_ECHO_TAIL_MARGIN_S", 0.2)
    monkeypatch.setattr(
        "jarvis.audio.capture.last_input_latency_s", lambda: 0.3
    )
    output_finished = asyncio.Event()
    echo_pcm = b"\x01\x00" * 32  # 0.05 s after the turn: echo either way
    tail_pcm = b"\x02\x00" * 32  # 0.35 s: safe on a fast mic, echo on this one
    live_pcm = b"\x04\x00" * 32  # past the whole tail: normal pass-through
    detector_inputs: list[bytes] = []

    class _Detector:
        def __init__(self, **_kwargs: object) -> None:
            self.active = False

        def warmup(self) -> None:
            return None

        def start_output(self) -> None:
            self.active = True

        def stop_output(self) -> None:
            self.active = False

        def feed(self, pcm: bytes) -> bytes | None:
            detector_inputs.append(pcm)
            return None

    class _Mic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def stream(self):
            await output_finished.wait()
            await asyncio.sleep(0.05)
            yield AudioChunk(pcm=echo_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.sleep(0.3)
            yield AudioChunk(pcm=tail_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.sleep(0.45)
            yield AudioChunk(pcm=live_pcm, sample_rate=16_000, timestamp_ns=0)
            await asyncio.Event().wait()

    class _Session(_HandshakeOnlyRealtimeSession):
        def __init__(self, send_binary, send_json) -> None:
            super().__init__(send_binary, send_json)
            self.audio_frames: list[bytes] = []
            self.received_live = asyncio.Event()

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
                await self._send_json({"type": "turn_complete"})
                output_finished.set()

        async def handle_audio_frame(self, pcm: bytes) -> None:
            self.audio_frames.append(pcm)
            if pcm == live_pcm:
                self.received_live.set()

        async def wait_finished(self) -> None:
            await self.received_live.wait()

    built: dict[str, object] = {}

    def _build(**kwargs):
        session = _Session(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        "jarvis.realtime.desktop.DesktopRealtimeBargeInDetector", _Detector
    )
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _Mic())

    await asyncio.wait_for(pipe._active_realtime_session(), timeout=4.0)

    session = built["session"]
    # Both early frames were inspected locally...
    assert detector_inputs == [echo_pcm, tail_pcm]
    # ...but only the frame recorded after the assistant had truly fallen
    # silent reached the provider. Without the capture latency, tail_pcm was
    # uploaded and the model answered its own echo.
    assert session.audio_frames == [live_pcm]


@pytest.mark.asyncio
async def test_thinking_phase_barge_in_takes_the_floor_without_a_stop_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Speaking into the SILENT wait interrupts, exactly like speaking over him.

    The reported failure (live 2026-08-13 12:11:12): barge-in during playback
    works perfectly, barge-in while Jarvis thinks does nothing. The playback
    detector is armed only inside the ``echo_guard_active`` branch, so during
    the silent wait there was no local detector at all and the only remaining
    signal — the provider's own VAD — is deliberately parked while an action
    runs. No stop word is spoken here on purpose: ordinary speech has to be
    enough.
    """
    pipe = _pipe()
    ready = asyncio.Event()
    captured = b"\x0b\x00" * 32

    class _Detector:
        """Confirms on any frame — the arming logic is what is under test."""

        def __init__(self, **kwargs: object) -> None:
            self._probe = kwargs.get("output_active")
            self.active = False

        def warmup(self) -> None:
            return None

        def start_output(self) -> None:
            self.active = True

        def stop_output(self) -> None:
            self.active = False

        def feed(self, _pcm: bytes) -> bytes | None:
            # Only the probe-less (thinking) detector ever confirms, mirroring
            # the real gate: with a playback probe attached feed() returns None
            # while nothing is playing.
            return None if self._probe is not None else captured

    class _Mic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def stream(self):
            await ready.wait()
            # Enough frames to clear _THINKING_BARGE_QUIET_S at 32 ms/frame.
            for _ in range(40):
                yield AudioChunk(
                    pcm=b"\x01\x00" * 256, sample_rate=16_000, timestamp_ns=0
                )
                await asyncio.sleep(0.02)
            await asyncio.Event().wait()

    class _ThinkingSession(_HandshakeOnlyRealtimeSession):
        """Owes a reply, nothing audible — the phase under test."""

        def __init__(self, send_binary, send_json) -> None:
            super().__init__(send_binary, send_json)
            self.audio_frames: list[bytes] = []
            self.barged = asyncio.Event()

        def owes_the_user_a_reply(self) -> bool:
            return True

        def _user_is_speaking(self) -> bool:
            return False

        async def handle_control(self, message) -> None:
            await super().handle_control(message)
            if message.get("type") == "barge_in":
                self.barged.set()

        async def handle_audio_frame(self, pcm: bytes) -> None:
            self.audio_frames.append(pcm)

        async def wait_finished(self) -> None:
            await self.barged.wait()

    built: dict[str, object] = {}

    def _build(**kwargs):
        session = _ThinkingSession(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        ready.set()
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        "jarvis.realtime.desktop.DesktopRealtimeBargeInDetector", _Detector
    )
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _Mic())

    await asyncio.wait_for(pipe._active_realtime_session(), timeout=5.0)

    session = built["session"]
    assert {"type": "barge_in"} in session.controls, (
        "speaking during the thinking phase did not interrupt"
    )
    # ...and what the user said reaches the provider, or the interruption
    # would take the floor and deliver no context.
    assert captured in session.audio_frames


@pytest.mark.asyncio
async def test_thinking_barge_stays_disarmed_while_the_user_is_still_talking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's own trailing words must not arm the barge against themselves.

    A provider that commits on its own VAD cuts mid-sentence when a filler
    pause fools it. That shape is turn fragmentation (872b05051), not an
    interruption, and firing a barge there would abandon the order the user is
    still in the middle of giving.
    """
    pipe = _pipe()
    ready = asyncio.Event()

    class _Detector:
        def __init__(self, **kwargs: object) -> None:
            self._probe = kwargs.get("output_active")
            self.active = False
            self.fed = 0

        def warmup(self) -> None:
            return None

        def start_output(self) -> None:
            self.active = True

        def stop_output(self) -> None:
            self.active = False

        def feed(self, _pcm: bytes) -> bytes | None:
            self.fed += 1
            return None if self._probe is not None else b"\x0c\x00" * 32

    class _Mic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def stream(self):
            await ready.wait()
            for _ in range(30):
                yield AudioChunk(
                    pcm=b"\x01\x00" * 256, sample_rate=16_000, timestamp_ns=0
                )
                await asyncio.sleep(0.02)
            await asyncio.Event().wait()

    class _StillTalkingSession(_HandshakeOnlyRealtimeSession):
        def __init__(self, send_binary, send_json) -> None:
            super().__init__(send_binary, send_json)
            self.audio_frames: list[bytes] = []
            self.settled = asyncio.Event()

        def owes_the_user_a_reply(self) -> bool:
            return True

        def _user_is_speaking(self) -> bool:
            return True

        async def handle_audio_frame(self, pcm: bytes) -> None:
            self.audio_frames.append(pcm)
            if len(self.audio_frames) >= 20:
                self.settled.set()

        async def wait_finished(self) -> None:
            await self.settled.wait()

    built: dict[str, object] = {}

    def _build(**kwargs):
        session = _StillTalkingSession(kwargs["send_binary"], kwargs["send_json"])
        built["session"] = session
        ready.set()
        return session

    monkeypatch.setattr("jarvis.realtime.factory.build_realtime_session", _build)
    monkeypatch.setattr(
        "jarvis.realtime.desktop.DesktopRealtimeBargeInDetector", _Detector
    )
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kwargs: _Mic())

    await asyncio.wait_for(pipe._active_realtime_session(), timeout=5.0)

    session = built["session"]
    assert {"type": "barge_in"} not in session.controls, (
        "a still-running utterance was mistaken for an interruption"
    )
    # The audio still flows: withholding it is what would lose the tail.
    assert session.audio_frames
