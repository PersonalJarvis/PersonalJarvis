"""Codex ChatGPT-subscription realtime adapter and SDP broker tests."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest

import jarvis.plugins.realtime.codex_subscription as codex_subscription_mod
from jarvis.plugins.realtime.codex_subscription import (
    CodexSubscriptionRealtimeProvider,
)
from jarvis.realtime.offer_broker import RealtimeTransportOfferBroker
from jarvis.realtime.protocol import RealtimeSessionConfig


class _Notification:
    def __init__(self, method: str, params: dict) -> None:
        self.method = method
        self.params = params


class _Subscription:
    def __init__(self, events=(), *, failure: BaseException | None = None) -> None:
        self.events = list(events)
        self.failure = failure
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure
        if not self.events:
            raise StopAsyncIteration
        return self.events.pop(0)

    def close(self) -> None:
        self.closed = True


class _ScheduledSubscription(_Subscription):
    def __init__(self, scheduled_events) -> None:
        super().__init__()
        self.scheduled_events = list(scheduled_events)

    async def __anext__(self):
        if not self.scheduled_events:
            raise StopAsyncIteration
        delay_s, event = self.scheduled_events.pop(0)
        if delay_s:
            await asyncio.sleep(delay_s)
        return event


class _CountingSubscription(_Subscription):
    def __init__(self) -> None:
        super().__init__()
        self.yielded = 0

    async def __anext__(self):
        self.yielded += 1
        return _Notification(
            "thread/realtime/transcript/delta",
            {
                "threadId": "thread-1",
                "role": "user",
                "delta": str(self.yielded),
            },
        )


class _FakeAudioEndpoint:
    """Stands in for the in-process WebRTC peer.

    ChatGPT-Live carries audio on the media track, so the adapter owns a real
    peer in production. Tests inject this to keep the suite off the network
    while still pinning that microphone PCM reaches the media path and that
    remote audio re-enters the normalized event stream.
    """

    def __init__(
        self,
        *,
        output_chunks: tuple[bytes, ...] = (),
        output_schedule: tuple[tuple[float, bytes], ...] = (),
    ) -> None:
        self.sent: list[tuple[bytes, int]] = []
        self.answers: list[str] = []
        self.closed = False
        self.connected = False
        # ``output_schedule`` paces chunks like real RTP arrival, which is what
        # the quiescence timer reacts to.
        self._schedule = list(output_schedule)
        self._outputs = asyncio.Queue()
        for chunk in output_chunks:
            self._outputs.put_nowait(chunk)
        if not output_schedule:
            self._outputs.put_nowait(None)

    async def create_offer(self) -> str:
        return "v=0\r\no=python-peer\r\n"

    async def apply_answer(self, answer_sdp: str) -> None:
        self.answers.append(answer_sdp)

    async def wait_connected(self, timeout_s: float = 15.0) -> None:
        del timeout_s
        self.connected = True

    def send_pcm(self, pcm: bytes, sample_rate: int) -> None:
        self.sent.append((pcm, sample_rate))

    async def next_output_pcm(self):
        if self._schedule:
            delay, chunk = self._schedule.pop(0)
            if delay:
                await asyncio.sleep(delay)
            return chunk
        return await self._outputs.get()

    async def close(self) -> None:
        self.closed = True


def _provider(client, **kwargs):
    """Build the provider with an injected fake media endpoint."""
    endpoint = kwargs.pop("endpoint", None) or _FakeAudioEndpoint()
    provider = CodexSubscriptionRealtimeProvider(
        client=client,
        # The factory receives the ICE configuration for this attempt.
        audio_endpoint_factory=lambda _ice=None: endpoint,
        **kwargs,
    )
    provider.test_endpoint = endpoint
    return provider


class _Client:
    def __init__(
        self,
        events=(),
        *,
        failure: BaseException | None = None,
        start_response: dict | None = None,
    ) -> None:
        self.subscription = _Subscription(events, failure=failure)
        self.thread_starts: list[dict] = []
        self.realtime_starts: list[tuple[str, dict]] = []
        self.audio_appends: list[tuple[str, dict]] = []
        self.text_appends: list[tuple[str, str, str]] = []
        self.speech_appends: list[tuple[str, str]] = []
        self.stops: list[str] = []
        self.interrupts: list[tuple[str, str]] = []
        self.unsubscribes: list[str] = []
        self.poison_calls = 0
        self.capability_calls = 0
        self._thread_number = 0
        self.start_response = dict(start_response or {})

    async def capability_status(self):
        self.capability_calls += 1
        return SimpleNamespace(
            available=True,
            chatgpt_authenticated=True,
            reason="",
        )

    async def thread_start(self, **kwargs):
        self.thread_starts.append(kwargs)
        self._thread_number += 1
        return {"thread": {"id": f"thread-{self._thread_number}"}}

    def subscribe(self, thread_id: str):
        del thread_id
        return self.subscription

    async def realtime_start(self, thread_id: str, **kwargs):
        self.realtime_starts.append((thread_id, kwargs))
        return SimpleNamespace(
            response=self.start_response,
            answer_sdp="v=0\r\nanswer",
        )

    async def realtime_append_audio(self, thread_id: str, **kwargs):
        self.audio_appends.append((thread_id, kwargs))
        return {}

    async def realtime_append_text(
        self, thread_id: str, text: str, *, role: str = "user"
    ):
        self.text_appends.append((thread_id, text, role))
        return {}

    async def realtime_append_speech(self, thread_id: str, text: str):
        self.speech_appends.append((thread_id, text))
        return {}

    async def turn_interrupt(self, thread_id: str, turn_id: str):
        self.interrupts.append((thread_id, turn_id))
        return {}

    async def realtime_stop(self, thread_id: str):
        self.stops.append(thread_id)
        return {}

    async def thread_unsubscribe(self, thread_id: str):
        self.unsubscribes.append(thread_id)
        return {}

    async def poison(self) -> None:
        self.poison_calls += 1


class _NoOfferBroker:
    async def acquire(self, *, timeout_s: float):
        del timeout_s
        return None


class _UnauthenticatedClient(_Client):
    async def thread_start(self, **kwargs):
        self.thread_starts.append(kwargs)
        raise RuntimeError("Codex app-server rejected the unauthenticated account")


class _FailedCleanupClient(_Client):
    async def realtime_stop(self, thread_id: str):
        self.stops.append(thread_id)
        raise RuntimeError("stop failed")


class _TimedOutCleanupClient(_Client):
    async def realtime_stop(self, thread_id: str):
        self.stops.append(thread_id)
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_direct_sdp_open_uses_safe_experimental_transport_contract() -> None:
    client = _Client()
    provider = _provider(client)

    session = await provider.open_session(
        RealtimeSessionConfig(
            instructions="Speak concise English.",
            transport_offer_sdp="v=0\r\no=browser-offer",
        )
    )

    assert session.answer_sdp == "v=0\r\nanswer"
    assert client.capability_calls == 0
    assert len(client.thread_starts) == 1
    thread_start = client.thread_starts[0]
    assert thread_start["ephemeral"] is True
    assert "extra" not in thread_start
    # The execution boundary is the security invariant: the Codex agent is
    # the VOICE, never the executor — every action goes out as a handoff.
    base = thread_start["base_instructions"].lower()
    developer = thread_start["developer_instructions"].lower()
    assert "never run tools" in base
    assert "handoff" in base
    assert "do not call tools" in developer
    assert "filesystem" in developer
    # …but it must no longer be told it is a dumb pipe: without an identity
    # the voice knew nothing about its own project.
    assert "persona" in base

    # Jarvis's own persona/context reaches the model as developer context —
    # ChatGPT-Live has no client-settable session-instructions field.
    assert client.text_appends == [
        ("thread-1", "Speak concise English.", "developer")
    ]
    _thread_id, start = client.realtime_starts[0]
    assert start == {
        "output_modality": "audio",
        # Jarvis's own peer produced this offer: ChatGPT-Live carries the
        # audio itself, which a signalling-only UI offer cannot do.
        "offer_sdp": "v=0\r\no=python-peer\r\n",
        "prompt": "",
        # v3 (ChatGPT-Live): the server chooses the model — the client must
        # not send one (rejected with "Field `session.model` is not allowed",
        # verified live 2026-08-01).
        "model": None,
        "voice": "cove",
        "version": "v3",
        "include_startup_context": False,
        "client_managed_handoffs": True,
    }
    await session.close()
    assert client.unsubscribes == ["thread-1"]


@pytest.mark.asyncio
async def test_failed_remote_cleanup_poisons_the_entire_app_server_client() -> None:
    client = _FailedCleanupClient()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    await session.close()

    assert client.stops == ["thread-1"]
    assert client.unsubscribes == ["thread-1"]
    assert client.poison_calls == 1


@pytest.mark.asyncio
async def test_timed_out_remote_cleanup_poisons_the_entire_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_subscription_mod, "_REMOTE_CLEANUP_TIMEOUT_S", 0.01)
    client = _TimedOutCleanupClient()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    await session.close()

    assert client.unsubscribes == ["thread-1"]
    assert client.poison_calls == 1


@pytest.mark.asyncio
async def test_stale_api_voice_selection_fails_before_subscription_realtime_start() -> None:
    """A voice outside the server-confirmed v3 roster fails before start."""
    client = _Client()
    provider = _provider(client)
    config = RealtimeSessionConfig(
        transport_offer_sdp="v=0\r\no=browser-offer",
        voice="marin",
    )

    with pytest.raises(RuntimeError, match="unsupported voice"):
        await provider.open_session(config)

    assert client.realtime_starts == []
    assert client.stops == ["thread-1"]
    assert client.unsubscribes == ["thread-1"]


@pytest.mark.asyncio
async def test_stale_model_pin_is_ignored_not_fatal() -> None:
    """v3 chooses the model server-side: a leftover pin from the metered API
    (or the dead v1 era) must not brick the call — it is dropped, and no
    model field reaches the server."""
    client = _Client()
    provider = _provider(client)
    config = RealtimeSessionConfig(
        transport_offer_sdp="v=0\r\no=browser-offer",
        model="gpt-4o-realtime-preview",
    )

    session = await provider.open_session(config)

    assert len(client.realtime_starts) == 1
    _thread, start = client.realtime_starts[0]
    assert start["model"] is None
    assert start["version"] == "v3"
    await session.close()


@pytest.mark.asyncio
async def test_language_update_is_developer_context_and_speech_is_authoritative() -> None:
    client = _Client()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    await session.update_session(language="es")
    await session.send_speech("Trusted answer")

    assert client.text_appends == [
        (
            "thread-1",
            codex_subscription_mod._LANGUAGE_UPDATE_TEXT["es"],
            "developer",
        )
    ]
    assert client.speech_appends == [("thread-1", "Trusted answer")]
    await session.close()


@pytest.mark.asyncio
async def test_open_relies_on_authoritative_app_server_auth_without_pre_probe() -> None:
    client = _UnauthenticatedClient()
    provider = _provider(client)

    with pytest.raises(RuntimeError, match="unauthenticated account"):
        await provider.open_session(
            RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
        )

    assert client.capability_calls == 0
    assert len(client.thread_starts) == 1
    assert client.realtime_starts == []


@pytest.mark.asyncio
async def test_orchestrator_capability_check_does_not_repeat_cli_auth_probe(
    monkeypatch,
) -> None:
    provider = _provider(_Client())

    def _unexpected_probe(_cls):
        raise AssertionError("factory login probe must not repeat during handshake")

    monkeypatch.setattr(
        CodexSubscriptionRealtimeProvider,
        "external_login_ready",
        classmethod(_unexpected_probe),
    )

    assert await provider.can_open_duplex_session() is True


def test_external_login_ready_respects_the_activation_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanently refused account must not be advertised as available."""
    from jarvis import codex_app_server

    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_activation_block",
        lambda: "Subscription voice permits only personal ChatGPT accounts.",
    )
    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary: pytest.fail("a blocked account needs no CLI probe"),
    )

    assert CodexSubscriptionRealtimeProvider.external_login_ready(None) is False


@pytest.mark.asyncio
async def test_verify_activation_keeps_successful_cold_transport_warm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider selection warms the transport the first call will reuse."""
    from jarvis import codex_app_server

    events: list[str] = []

    class _Client:
        async def require_chatgpt_login(self) -> None:
            events.append("verified")

        async def close(self) -> None:
            events.append("closed")

    monkeypatch.setattr(
        codex_app_server,
        "get_shared_codex_app_server",
        lambda _binary: _Client(),
    )

    await CodexSubscriptionRealtimeProvider.verify_activation(SimpleNamespace())

    assert events == ["verified"]


@pytest.mark.asyncio
async def test_verify_activation_cleans_up_a_failed_cold_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    events: list[str] = []

    class _Client:
        async def require_chatgpt_login(self) -> None:
            events.append("failed")
            raise RuntimeError("login failed")

        async def close(self) -> None:
            events.append("closed")

    monkeypatch.setattr(
        codex_app_server,
        "get_shared_codex_app_server",
        lambda _binary: _Client(),
    )

    with pytest.raises(RuntimeError, match="login failed"):
        await CodexSubscriptionRealtimeProvider.verify_activation(SimpleNamespace())

    assert events == ["failed", "closed"]


@pytest.mark.asyncio
async def test_verify_activation_never_closes_a_client_carrying_a_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that was ALREADY ready is carrying a live session — verifying
    is harmless, closing it would cut the call mid-sentence."""
    from jarvis import codex_app_server

    events: list[str] = []

    class _Client:
        ready = True

        async def require_chatgpt_login(self) -> None:
            events.append("verified")

        async def close(self) -> None:
            events.append("closed")

    monkeypatch.setattr(
        codex_app_server,
        "get_shared_codex_app_server",
        lambda _binary: _Client(),
    )

    await CodexSubscriptionRealtimeProvider.verify_activation(SimpleNamespace())

    assert events == ["verified"]


@pytest.mark.parametrize(
    ("reason_code", "available", "authenticated", "expected"),
    [
        ("ready", True, True, True),
        ("login_required", False, False, False),
        # Transiently unknown must fail OPEN: voice-mode availability flips
        # (with a user-facing 400) on a fail-closed answer, while opening a
        # session performs the authoritative live account verification anyway.
        ("busy", False, False, True),
    ],
)
def test_external_login_ready_fails_open_on_transient_busy(
    monkeypatch: pytest.MonkeyPatch,
    reason_code: str,
    available: bool,
    authenticated: bool,
    expected: bool,
) -> None:
    from jarvis import codex_app_server

    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary: SimpleNamespace(
            available=available,
            chatgpt_authenticated=authenticated,
            reason_code=reason_code,
        ),
    )

    assert (
        CodexSubscriptionRealtimeProvider.external_login_ready(None) is expected
    )


@pytest.mark.asyncio
async def test_send_audio_rides_the_media_track_not_a_sideband_append() -> None:
    """ChatGPT-Live has NO audio client event: the sideband append the retired
    v1 protocol used is rejected outright, so microphone PCM must reach the
    WebRTC media path instead."""
    client = _Client()
    provider = _provider(client)
    session = await provider.open_session(RealtimeSessionConfig())

    await session.send_audio(
        SimpleNamespace(pcm=b"\x01\x00\x02\x00", sample_rate=24_000, channels=1)
    )

    assert provider.test_endpoint.sent == [(b"\x01\x00\x02\x00", 24_000)]
    assert client.audio_appends == []
    await session.close()
    assert provider.test_endpoint.closed is True


@pytest.mark.asyncio
async def test_notifications_normalize_audio_transcripts_and_boundaries() -> None:
    client = _Client(
        [
            _Notification(
                "thread/realtime/itemAdded",
                {
                    "threadId": "thread-1",
                    "item": {
                        "type": "input_audio_buffer.speech_started",
                        "item_id": "input-1",
                    },
                },
            ),
            _Notification(
                "thread/realtime/transcript/delta",
                {"threadId": "thread-1", "role": "user", "delta": "hello"},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "user", "text": "hello"},
            ),
            _Notification(
                "thread/realtime/transcript/delta",
                {"threadId": "thread-1", "role": "assistant", "delta": "hi"},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "hi"},
            ),
        ]
    )
    # The user really was speaking, so their transcripts pass the energy gate.
    session = await _provider(
        client, input_transcriber_factory=lambda: _StubEndpointer(speaking=True)
    ).open_session(RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer"))

    events = [event async for event in session.receive()]

    # No audio_delta here on purpose: ChatGPT-Live carries assistant audio on
    # the WebRTC media track, never as a sideband notification.
    assert [event.type for event in events] == [
        "speech_started",
        "input_transcript",
        "input_transcript",
        "output_transcript_delta",
        "turn_complete",
        "error",
    ]
    # A live preview, not the recorded turn: with a local recognizer present it
    # owns the final text (see the server-transcript tests at the end of this
    # file for both halves of that rule).
    assert events[2].is_final is False
    assert events[2].item_id == "input-1"
    assert events[3].text == "hi"
    # A cleanly exhausted fake stream is still an unexpected transport death.
    assert "ended unexpectedly" in str(events[-1].error)
    await session.close()


@pytest.mark.asyncio
async def test_media_track_audio_becomes_normalized_audio_deltas() -> None:
    """Assistant audio arrives as RTP on ChatGPT-Live; it must reach the
    pipeline as ordinary audio_delta events so playback, the Orb, and the
    transcript-gated release keep working exactly as before."""
    client = _Client()
    # The notification stream stays open past the audio, exactly as it does
    # during a live call — otherwise the fake transport death would race the
    # media pump.
    client.subscription = _ScheduledSubscription(
        [
            (
                0.05,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "hi"},
                ),
            )
        ]
    )
    endpoint = _FakeAudioEndpoint(output_chunks=(b"\x01\x00\x02\x00", b"\x03\x00"))
    session = await _provider(client, endpoint=endpoint).open_session(
        RealtimeSessionConfig()
    )

    audio = [
        event async for event in session.receive() if event.type == "audio_delta"
    ]

    assert [event.audio.pcm for event in audio] == [b"\x01\x00\x02\x00", b"\x03\x00"]
    assert {event.audio.sample_rate for event in audio} == {24_000}
    assert {event.audio.channels for event in audio} == {1}
    await session.close()


@pytest.mark.asyncio
async def test_media_track_forwards_provider_silence_verbatim() -> None:
    """The reply's own pauses must survive the trip to the speaker.

    They used to be compressed away, which was right for the retired sideband
    protocol (audio arrived faster than realtime, so dropping silence really
    did shorten the wait). On a live WebRTC track it removes audio the player
    needed in order to keep playing: the output stream starves and the voice
    chops. Measured on 2026-08-02: six cuts inside a single answer.
    """
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.05,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "hi"},
                ),
            )
        ]
    )
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    long_silence = b"\x00\x00" * (24_000 * 2)
    endpoint = _FakeAudioEndpoint(output_chunks=(speech, long_silence, speech))
    session = await _provider(client, endpoint=endpoint).open_session(
        RealtimeSessionConfig()
    )

    audio = [
        event.audio.pcm
        async for event in session.receive()
        if event.type == "audio_delta"
    ]

    assert b"".join(audio) == speech + long_silence + speech
    await session.close()


@pytest.mark.asyncio
async def test_default_done_waits_for_all_late_audio_before_one_completion(
    monkeypatch,
) -> None:
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.03)
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "hi"},
                ),
            ),
            # Keeps the notification stream open while the late media audio
            # arrives, as a live transport does.
            (0.2, _Notification("thread/realtime/keepalive", {"threadId": "thread-1"})),
        ]
    )
    # Late RTP audio must keep re-arming the quiescence timer so one turn ends
    # exactly once, after the last audible chunk.
    endpoint = _FakeAudioEndpoint(
        output_schedule=((0.015, b"\x01\x00"), (0.015, b"\x01\x00"), (0.0, None))
    )
    session = await _provider(client, endpoint=endpoint).open_session(
        RealtimeSessionConfig()
    )

    events = [event async for event in session.receive()]
    event_types = [event.type for event in events]

    assert event_types.count("audio_delta") == 2
    assert event_types.count("turn_complete") == 1
    assert max(
        index for index, event_type in enumerate(event_types) if event_type == "audio_delta"
    ) < event_types.index("turn_complete")
    await session.close()


@pytest.mark.asyncio
async def test_later_assistant_delta_cancels_pending_completion_until_next_done(
    monkeypatch,
) -> None:
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.02)
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "part"},
                ),
            ),
            (
                0.01,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {"threadId": "thread-1", "role": "assistant", "delta": "more"},
                ),
            ),
        ]
    )
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    assert not [event for event in events if event.type == "turn_complete"]
    await session.close()


@pytest.mark.parametrize(
    ("method", "params"),
    [
        (
            "thread/realtime/itemAdded",
            {"item": {"type": "input_audio_buffer.speech_started"}},
        ),
        (
            "thread/realtime/itemAdded",
            {"item": {"type": "response.cancelled"}},
        ),
        ("thread/realtime/error", {"message": "transport failed"}),
        ("thread/realtime/closed", {"reason": "closed"}),
    ],
)
@pytest.mark.asyncio
async def test_terminal_or_interruption_event_cancels_pending_completion(
    monkeypatch,
    method: str,
    params: dict,
) -> None:
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.01)
    done = _Notification(
        "thread/realtime/transcript/done",
        {"threadId": "thread-1", "role": "assistant", "text": "partial"},
    )
    cancelling = _Notification(method, {"threadId": "thread-1", **params})
    client = _Client([done, cancelling])
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    assert not [event for event in events if event.type == "turn_complete"]
    await session.close()


@pytest.mark.asyncio
async def test_duplicate_transcript_done_emits_one_completion(monkeypatch) -> None:
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.01)
    done = _Notification(
        "thread/realtime/transcript/done",
        {"threadId": "thread-1", "role": "assistant", "text": "done"},
    )
    client = _Client([done, done])
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    assert len([event for event in events if event.type == "turn_complete"]) == 1
    await session.close()


@pytest.mark.asyncio
async def test_handoff_without_transcript_preserves_neutral_boundary() -> None:
    client = _Client(
        [
            _Notification(
                "thread/realtime/itemAdded",
                {
                    "threadId": "thread-1",
                    "item": {"type": "handoff_request", "handoff_id": "handoff-1"},
                },
            )
        ]
    )
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    assert not [event for event in events if event.type == "turn_complete"]
    handoff = next(event for event in events if event.type == "handoff_requested")
    assert handoff.handoff_id == "handoff-1"
    assert handoff.text is None
    await session.close()


@pytest.mark.asyncio
async def test_handoff_preserves_item_transcript_and_interrupts_late_codex_turn() -> None:
    client = _Client(
        [
            _Notification(
                "thread/realtime/itemAdded",
                {
                    "threadId": "thread-1",
                    "item": {
                        "type": "handoff_request",
                        "handoff_id": "handoff-1",
                        "input_transcript": "Please check the calendar",
                        "active_transcript": [
                            {"role": "user", "text": "older question"}
                        ],
                    },
                },
            ),
            _Notification(
                "turn/started",
                {
                    "threadId": "thread-1",
                    "turn": {"id": "codex-turn-1"},
                },
            ),
        ]
    )
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    handoff = next(event for event in events if event.type == "handoff_requested")
    assert handoff.text == "Please check the calendar"
    assert handoff.handoff_id == "handoff-1"
    assert client.interrupts == [("thread-1", "codex-turn-1")]
    await session.close()


@pytest.mark.asyncio
async def test_done_then_handoff_cancels_synthetic_completion(monkeypatch) -> None:
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.01)
    client = _Client(
        [
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "partial"},
            ),
            _Notification(
                "thread/realtime/itemAdded",
                {
                    "threadId": "thread-1",
                    "item": {"type": "handoff_request", "handoff_id": "handoff-1"},
                },
            ),
        ]
    )
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    assert not [event for event in events if event.type == "turn_complete"]
    assert any(event.type == "handoff_requested" for event in events)
    await session.close()


@pytest.mark.asyncio
async def test_a_terminal_response_item_ends_the_turn_at_once(monkeypatch) -> None:
    """The server says when a response is over; Jarvis must not guess.

    The quiescence backstop is pushed out of reach here, so a completion can
    only come from the terminal item itself.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 10.0)
    client = _Client(
        [
            _Notification(
                "thread/realtime/started",
                {"threadId": "thread-1", "version": 3},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "done"},
            ),
            _Notification(
                "thread/realtime/itemAdded",
                {"threadId": "thread-1", "item": {"type": "response.done"}},
            ),
        ]
    )
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    async with asyncio.timeout(0.2):
        events = [event async for event in session.receive()]

    assert len([event for event in events if event.type == "turn_complete"]) == 1
    assert session.realtime_version == "3"
    await session.close()


@pytest.mark.asyncio
async def test_transcript_done_is_a_part_boundary_not_a_turn(monkeypatch) -> None:
    """One answer, one ending.

    ChatGPT-Live streams a reply as several transcript parts. Treating each
    part's ``done`` as the end of the turn drained playback, armed a ~0.9 s
    echo window and re-armed the scrub gate three times inside ONE answer
    (measured live 2026-08-02) — the chopped voice, plus a microphone that went
    deaf between the pieces.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 10.0)
    parts = [
        _Notification(
            "thread/realtime/started",
            {"threadId": "thread-1", "version": 3},
        )
    ]
    for text in ("first. ", "second. ", "third."):
        parts.append(
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": text},
            )
        )
    parts.append(
        _Notification(
            "thread/realtime/itemAdded",
            {"threadId": "thread-1", "item": {"type": "response.done"}},
        )
    )
    session = await _provider(_Client(parts)).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    async with asyncio.timeout(0.2):
        events = [event async for event in session.receive()]

    assert len([event for event in events if event.type == "turn_complete"]) == 1
    # Every part still reaches the transcript consumers.
    assert [
        event.text
        for event in events
        if event.type == "output_transcript_delta"
    ] == ["first. ", "second. ", "third."]
    await session.close()


@pytest.mark.asyncio
async def test_normalization_queue_backpressures_and_pump_cleans_up() -> None:
    client = _Client()
    subscription = _CountingSubscription()
    client.subscription = subscription
    # This subscription streams user transcripts forever, so the endpointer has
    # to report real speech - otherwise the energy gate rightly discards every
    # one of them and there is no backpressure left to measure.
    session = await _provider(
        client, input_transcriber_factory=lambda: _StubEndpointer(speaking=True)
    ).open_session(RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer"))
    stream = session.receive()

    first = await anext(stream)
    assert first.type == "input_transcript"
    await asyncio.sleep(0.01)

    assert subscription.yielded <= codex_subscription_mod._NORMALIZATION_QUEUE_MAX + 2
    yielded_before_close = subscription.yielded
    await stream.aclose()
    await asyncio.sleep(0)
    assert subscription.yielded == yielded_before_close
    await session.close()


@pytest.mark.asyncio
async def test_handoff_request_never_becomes_a_direct_tool_call() -> None:
    client = _Client(
        [
            _Notification(
                "thread/realtime/itemAdded",
                {
                    "threadId": "thread-1",
                    "item": {
                        "type": "handoff_request",
                        "handoff_id": "handoff-1",
                    },
                },
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "Working"},
            ),
        ]
    )
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    assert not [event for event in events if event.type == "tool_call"]
    await session.close()


@pytest.mark.asyncio
async def test_provider_error_and_app_server_death_are_normalized() -> None:
    provider_error_client = _Client(
        [
            _Notification(
                "thread/realtime/error",
                {"threadId": "thread-1", "message": "experimental transport failed"},
            )
        ]
    )
    provider_session = await _provider(provider_error_client).open_session(
        RealtimeSessionConfig()
    )
    provider_events = [event async for event in provider_session.receive()]
    assert provider_events[0].type == "error"
    assert provider_events[0].recoverable is False

    dead_client = _Client(failure=RuntimeError("process exited"))
    dead_session = await _provider(dead_client).open_session(
        RealtimeSessionConfig()
    )
    dead_events = [event async for event in dead_session.receive()]
    assert dead_events[0].type == "error"
    assert "notification stream failed" in str(dead_events[0].error)
    await provider_session.close()
    await dead_session.close()


@pytest.mark.asyncio
async def test_media_endpoint_failure_fails_before_app_server_launch() -> None:
    """No media path means no call: ChatGPT-Live audio IS the WebRTC track, so
    failing early beats a session that looks connected and stays mute."""
    client = _Client()

    class _BrokenEndpoint(_FakeAudioEndpoint):
        async def create_offer(self) -> str:
            raise RuntimeError("no local WebRTC endpoint")

    provider = _provider(client, endpoint=_BrokenEndpoint())

    with pytest.raises(RuntimeError, match="no local WebRTC endpoint"):
        await provider.open_session(RealtimeSessionConfig())

    assert client.capability_calls == 0
    assert client.thread_starts == []


@pytest.mark.asyncio
async def test_broker_keeps_answered_lease_until_provider_release() -> None:
    broker = RealtimeTransportOfferBroker()
    registration = await broker.register("offer-1", "v=0\r\no=one")
    lease = await broker.acquire()
    assert lease is not None

    assert await lease.answer("v=0\r\no=answer") is True
    answer = await registration.wait()
    assert answer.type == "answer"
    assert answer.answer_sdp == "v=0\r\no=answer"

    await lease.release()
    released = await registration.wait()
    assert released.type == "release"
    assert await broker.pending_count() == 0


@pytest.mark.asyncio
async def test_unconnectable_host_path_retries_with_stun() -> None:
    """Host candidates cost no gathering time and connect on an ordinary
    network; a network that needs a reflexive candidate must still work, so a
    dead media path is retried once WITH a STUN server."""
    from jarvis.realtime.webrtc_transport import WebRtcMediaPathUnavailable

    client = _Client()
    ice_configs: list[object] = []

    class _HostOnlyFails(_FakeAudioEndpoint):
        def __init__(self, ice_servers) -> None:
            super().__init__()
            self.ice_servers = ice_servers

        async def wait_connected(self, timeout_s: float = 15.0) -> None:
            del timeout_s
            if not self.ice_servers:
                raise WebRtcMediaPathUnavailable("media path failed")
            self.connected = True

    def factory(ice_servers=None) -> _HostOnlyFails:
        ice_configs.append(ice_servers)
        return _HostOnlyFails(ice_servers)

    provider = CodexSubscriptionRealtimeProvider(
        client=client, audio_endpoint_factory=factory
    )

    session = await provider.open_session(RealtimeSessionConfig())

    # First attempt host-only (no servers), second with STUN.
    assert ice_configs[0] is None
    assert ice_configs[1]
    assert len(ice_configs) == 2
    await session.close()


@pytest.mark.asyncio
async def test_each_session_negotiates_its_own_media_endpoint() -> None:
    """Every call owns a fresh peer: the answer is applied to it and the media
    path is proven live before the session is handed to the pipeline."""
    client = _Client()
    endpoints: list[_FakeAudioEndpoint] = []

    def factory(_ice_servers=None) -> _FakeAudioEndpoint:
        endpoint = _FakeAudioEndpoint()
        endpoints.append(endpoint)
        return endpoint

    provider = CodexSubscriptionRealtimeProvider(
        client=client, audio_endpoint_factory=factory
    )

    for _ in (1, 2):
        session = await provider.open_session(RealtimeSessionConfig())
        await session.close()

    assert len(endpoints) == 2
    assert all(endpoint.answers == ["v=0\r\nanswer"] for endpoint in endpoints)
    assert all(endpoint.connected for endpoint in endpoints)
    assert all(endpoint.closed for endpoint in endpoints)
    # The offer the provider sent is the one its OWN peer produced.
    assert [kwargs["offer_sdp"] for _thread, kwargs in client.realtime_starts] == [
        "v=0\r\no=python-peer\r\n",
        "v=0\r\no=python-peer\r\n",
    ]


@pytest.mark.asyncio
async def test_ui_disconnect_after_answer_removes_answered_lease() -> None:
    broker = RealtimeTransportOfferBroker()
    registration = await broker.register("offer-1", "v=0\r\no=one")
    lease = await broker.acquire()
    assert lease is not None
    assert await lease.answer("v=0\r\no=answer") is True
    assert (await registration.wait()).type == "answer"

    await registration.cancel()

    assert (await registration.wait()).type == "release"
    assert await broker.pending_count() == 0
    assert await lease.answer("v=0\r\no=late-answer") is False


class _StubEndpointer:
    """Stands in for the local endpointer's word-agnostic energy verdict."""

    def __init__(self, speaking: bool) -> None:
        self._speaking = speaking

    def speech_recently(self, grace_ms: int = 2000) -> bool:  # noqa: ARG002
        return self._speaking

    def feed(self, pcm, sample_rate) -> None:  # noqa: ANN001, ARG002 - protocol
        return None

    async def next_event(self):  # noqa: ANN202 - delivers nothing in these tests
        await asyncio.sleep(3600)

    async def close(self) -> None:
        return None


def _hallucination_client() -> _Client:
    return _Client(
        [
            _Notification(
                "thread/realtime/transcript/delta",
                {"threadId": "thread-1", "role": "user", "delta": "a_lee "},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {
                    "threadId": "thread-1",
                    "role": "user",
                    "text": "a_lee pixelated image",
                },
            ),
        ]
    )


async def _user_transcripts(*, speaking: bool | None) -> list:
    transcriber = None if speaking is None else _StubEndpointer(speaking)
    session = await _provider(
        _hallucination_client(), input_transcriber_factory=lambda: transcriber
    ).open_session(RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer"))
    events = [event async for event in session.receive()]
    await session.close()
    return [event for event in events if event.type == "input_transcript"]


@pytest.mark.asyncio
async def test_a_server_transcript_without_microphone_energy_never_becomes_a_turn():
    """The live defect: the user said "Hallo, was geht?" and Jarvis recorded
    "a_lee pixelated image" as their words, then answered it in earnest.
    ChatGPT-Live transcribes the user itself and, like every recognizer, writes
    caption-shaped text over silence and over the echo of its own voice. Only
    audio energy separates the two - a hallucination is spelled flawlessly
    (AP-27)."""
    assert await _user_transcripts(speaking=False) == []


@pytest.mark.asyncio
async def test_a_server_transcript_backed_by_speech_shows_up_live():
    """While the user really is speaking, the far end's transcript is the only
    live text there is - dropping it would leave the bar blank mid-sentence."""
    transcripts = await _user_transcripts(speaking=True)

    assert [event.text for event in transcripts] == [
        "a_lee ",
        "a_lee pixelated image",
    ]
    # The local recognizer - the user's own, with their dictionary and bias
    # prompt - owns the FINAL text, so these stay a live preview.
    assert not any(event.is_final for event in transcripts)


@pytest.mark.asyncio
async def test_without_a_local_recognizer_the_server_transcript_is_final():
    """A host with no usable recognizer still has to reach a routed turn, or
    the provider talks while every Jarvis integration sits idle."""
    finals = [event for event in await _user_transcripts(speaking=None) if event.is_final]

    assert [event.text for event in finals] == ["a_lee pixelated image"]


def _keeps_stream_open(*extra):
    """Notifications plus a late one, so the stream outlives the assertions."""
    return _ScheduledSubscription(
        [
            (
                0.0,
                _Notification(
                    "thread/realtime/started",
                    {"threadId": "thread-1", "version": 3},
                ),
            ),
            *extra,
            (
                2.0,
                _Notification(
                    "thread/realtime/started",
                    {"threadId": "thread-1", "version": 3},
                ),
            ),
        ]
    )


_ARMS_THE_BACKSTOP = (
    0.0,
    _Notification(
        "thread/realtime/transcript/done",
        {"threadId": "thread-1", "role": "assistant", "text": "hi"},
    ),
)


@pytest.mark.asyncio
async def test_silent_frames_never_hold_a_turn_open(monkeypatch) -> None:
    """The trap that makes the backstop the ONLY safe boundary.

    The media track keeps sending silence between turns. If silence re-armed
    the quiescence timer, that timer could never fire — and since the terminal
    response item's v3 spelling is not yet confirmed live, a turn would hang
    forever on any protocol that omits it.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.1)
    client = _Client()
    client.subscription = _keeps_stream_open(_ARMS_THE_BACKSTOP)
    silence = b"\x00\x00" * 480
    endpoint = _FakeAudioEndpoint(
        output_schedule=tuple((0.02, silence) for _ in range(25))
    )
    session = await _provider(client, endpoint=endpoint).open_session(
        RealtimeSessionConfig()
    )

    completions = 0
    async with asyncio.timeout(1.0):
        async for event in session.receive():
            if event.type == "turn_complete":
                completions += 1
                break

    assert completions == 1
    await session.close()


@pytest.mark.asyncio
async def test_audible_frames_keep_a_turn_open(monkeypatch) -> None:
    """The other half: real speech must not be cut off by the backstop.

    Paired with the test above on purpose — together they rule out an
    implementation that satisfies one by ignoring audio altogether.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.1)
    client = _Client()
    client.subscription = _keeps_stream_open(_ARMS_THE_BACKSTOP)
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(
        output_schedule=tuple((0.02, speech) for _ in range(25))
    )
    session = await _provider(client, endpoint=endpoint).open_session(
        RealtimeSessionConfig()
    )

    events = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(0.3):
            async for event in session.receive():
                events.append(event)

    assert [event for event in events if event.type == "audio_delta"]
    assert not [event for event in events if event.type == "turn_complete"]
    await session.close()


@pytest.mark.asyncio
async def test_an_unknown_realtime_item_type_is_named_once(caplog) -> None:
    """Silently swallowing unknown items is why neither the real terminal
    response item nor the never-observed handoff item could be identified from
    a whole call's log (AP-30). The name is logged; the payload never is."""
    caplog.set_level(logging.INFO)
    unknown = _Notification(
        "thread/realtime/itemAdded",
        {
            "threadId": "thread-1",
            "item": {"type": "response.output_audio.done", "secret": "transcript"},
        },
    )
    session = await _provider(_Client([unknown, unknown])).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

    events = [event async for event in session.receive()]

    assert not [event for event in events if event.type == "turn_complete"]
    named = [
        record.getMessage()
        for record in caplog.records
        if "unhandled realtime item type" in record.getMessage()
    ]
    assert len(named) == 1
    assert "response.output_audio.done" in named[0]
    assert "transcript" not in named[0]
    await session.close()


@pytest.mark.asyncio
async def test_the_negotiated_protocol_and_voice_are_logged(caplog) -> None:
    """"Is this really what Codex uses?" has to be answerable from evidence."""
    caplog.set_level(logging.INFO)
    client = _Client(
        [
            _Notification(
                "thread/realtime/started",
                {"threadId": "thread-1", "version": 3},
            )
        ]
    )
    session = await _provider(client).open_session(
        RealtimeSessionConfig(voice="cove")
    )

    [event async for event in session.receive()]

    assert any(
        "negotiated protocol 3" in record.getMessage()
        and "cove" in record.getMessage()
        for record in caplog.records
    )
    await session.close()
