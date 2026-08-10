"""Codex ChatGPT-subscription realtime adapter and SDP broker tests."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest

import jarvis.plugins.realtime.codex_subscription as codex_subscription_mod
from jarvis.codex_app_server import _MAX_REALTIME_INITIAL_TEXT_BYTES
from jarvis.plugins.realtime.codex_subscription import (
    CodexSubscriptionRealtimeProvider,
)
from jarvis.realtime.input_transcription import InputTranscriptEvent
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
    # Adapter tests do not reach the user's configured cloud/local STT unless
    # a case injects an explicit recognizer. The production default is covered
    # by the input-transcriber unit tests.
    kwargs.setdefault("input_transcriber_factory", lambda: None)
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

    async def realtime_append_text(self, thread_id: str, text: str, *, role: str = "user"):
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

    # Persona, language policy, and history are part of the atomic live start.
    # No developer append exists for the far end to acknowledge before the
    # user speaks.
    assert client.text_appends == []
    _thread_id, start = client.realtime_starts[0]
    assert start["output_modality"] == "audio"
    assert start["offer_sdp"] == "v=0\r\no=python-peer\r\n"
    assert start["prompt"] == ""
    assert start["initial_items"] == []
    assert start["model"] is None
    assert start["voice"] == "cove"
    assert start["version"] == "v3"
    assert start["include_startup_context"] is False
    assert start["client_managed_handoffs"] is True
    trusted_prompt = start["trusted_prompt"]
    assert "Speak concise English." in trusted_prompt
    assert "Speak only the assistant side" in trusted_prompt
    assert "Reply in the language the user actually speaks" in trusted_prompt
    assert "reply only in" not in trusted_prompt, "a hint is not a pin"
    await session.close()
    assert client.unsubscribes == ["thread-1"]


@pytest.mark.asyncio
async def test_reopened_session_restores_bounded_same_call_history() -> None:
    client = _Client()
    provider = _provider(client)
    history = (
        {"role": "user", "text": "Which language were we discussing?"},
        {"role": "assistant", "text": "We were discussing Malbolge."},
    )

    session = await provider.open_session(
        RealtimeSessionConfig(
            instructions="Speak concise English.",
            history=history,
        )
    )

    assert client.text_appends == []
    _thread_id, start = client.realtime_starts[0]
    assert start["initial_items"] == list(history)
    assert "Speak concise English." in start["trusted_prompt"]
    assert "Reply in the language the user actually speaks" in start["trusted_prompt"]
    await session.close()


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

    appended = [text for _thread, text, _role in client.text_appends]
    # The opening hint was atomic startup configuration; the only append is
    # the first real per-turn pin resolved upstream.
    assert appended == [codex_subscription_mod._language_pin_text("es")]
    assert client.speech_appends == [("thread-1", "Trusted answer")]
    await session.close()


@pytest.mark.asyncio
async def test_context_refresh_appends_only_the_changed_lines() -> None:
    """An append-only transport must not receive the full block per turn.

    The session refreshes its ~20k-char instruction block on every final
    user transcript (the clock line alone changes every minute); re-appending
    all of it grew the live thread by a whole persona per exchange.
    """
    client = _Client()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(instructions="Persona line.\nClock: 12:00.")
    )

    await session.update_session(instructions="Persona line.\nClock: 12:01.")

    context_appends = [
        text
        for _thread, text, role in client.text_appends
        if role == "developer" and ("Persona line." in text or "Clock" in text)
    ]
    assert len(context_appends) == 1
    update = context_appends[0]
    assert "Clock: 12:01." in update
    assert "Persona line." not in update
    assert "stay in force" in update
    await session.close()


@pytest.mark.asyncio
async def test_per_turn_update_travels_as_one_silent_developer_item() -> None:
    """Context delta, current-turn block and language pin ride ONE append.

    Every separate developer item was one more thing the model audibly
    acknowledged — a live call spliced "Alles klar…" into the middle of its
    own answer (2026-08-05 19:13). The payload also has to SAY the update is
    silent configuration, or the model treats it as conversation.
    """
    client = _Client()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(instructions="Persona line.\nClock: 12:00.")
    )
    baseline = len(client.text_appends)

    await session.update_session(
        instructions="Persona line.\nClock: 12:01.",
        language="de",
        turn_directive="Directive A.",
    )

    assert len(client.text_appends) == baseline + 1
    _thread, payload, role = client.text_appends[-1]
    assert role == "developer"
    assert "Clock: 12:01." in payload
    assert "Directive A." in payload
    assert codex_subscription_mod._language_pin_text("de") in payload
    assert "silently" in payload.lower()
    await session.close()


@pytest.mark.asyncio
async def test_turn_directive_supersedes_instead_of_accumulating() -> None:
    """A changed turn directive is delivered WHOLE, with replace semantics.

    The three per-turn directives are mutually exclusive; a line-diff plus a
    blanket "everything stays in force" left the thread claiming "answer
    directly", "do not answer" and "say you are still working" all at once
    (independent review 2026-08-05).
    """
    client = _Client()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(instructions="Persona line.\nDirective A.")
    )

    await session.update_session(
        instructions="Persona line.\nDirective B.",
        turn_directive="Directive B.",
    )
    await session.update_session(
        instructions="Persona line.\nDirective A.",
        turn_directive="Directive A.",
    )

    appends = [text for _thread, text, _role in client.text_appends]
    # Turn 2: the directive travels ONLY in its superseding section, never
    # additionally as a diffed context line.
    assert "REPLACE every earlier instruction" in appends[0]
    assert "Directive B." in appends[0]
    assert appends[0].count("Directive B.") == 1
    # Turn 3: reverting re-issues the directive as a full replacement even
    # though an identical copy already sits in the thread's opening block.
    assert "REPLACE every earlier instruction" in appends[1]
    assert "Directive A." in appends[1]
    assert "Directive B." not in appends[1]
    await session.close()


@pytest.mark.asyncio
async def test_open_pins_language_only_for_an_explicit_reply_language() -> None:
    """brain.reply_language = de must pin the FIRST reply, auto must not."""
    client = _Client()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(
            transport_offer_sdp="v=0\r\no=offer",
            language="de",
            language_is_pinned=True,
        )
    )

    assert client.text_appends == []
    _thread_id, start = client.realtime_starts[0]
    assert codex_subscription_mod._language_pin_text("de") in start["trusted_prompt"]
    await session.close()


@pytest.mark.asyncio
async def test_same_language_is_reasserted_at_every_local_turn_boundary() -> None:
    """Auto-response can start before a large persona refresh takes effect.

    The concise language directive must therefore be the final developer item
    on every grounded turn, even when the resolved language did not change.
    """
    client = _Client()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(
            language="de",
            language_is_pinned=True,
            transport_offer_sdp="v=0\r\no=offer",
        )
    )

    await session.update_session(language="de")

    pin = codex_subscription_mod._language_pin_text("de")
    assert client.text_appends == [("thread-1", pin, "developer")]
    await session.close()


@pytest.mark.asyncio
async def test_open_relies_on_authoritative_app_server_auth_without_pre_probe() -> None:
    client = _UnauthenticatedClient()
    provider = _provider(client)

    with pytest.raises(RuntimeError, match="unauthenticated account"):
        await provider.open_session(RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer"))

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

    assert CodexSubscriptionRealtimeProvider.external_login_ready(None) is expected


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
    # This normalization test deliberately omits the optional local recognizer;
    # local-grounding behavior has dedicated tests below.
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )

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
    # Without a local recognizer the server transcript owns the final text.
    assert events[2].is_final is True
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
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

    audio = [event async for event in session.receive() if event.type == "audio_delta"]

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
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

    audio = [event.audio.pcm async for event in session.receive() if event.type == "audio_delta"]

    assert b"".join(audio) == speech + long_silence + speech
    await session.close()


@pytest.mark.asyncio
async def test_default_done_waits_for_all_late_audio_before_one_completion(
    monkeypatch,
) -> None:
    # The last chunk must land WELL inside the quiescence window: Windows
    # timer granularity (~15.6 ms) makes two sequential sleeps overshoot a
    # same-length single sleep, so a schedule that races the deadline exactly
    # loses deterministically there while passing elsewhere.
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.25)
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
            (0.6, _Notification("thread/realtime/keepalive", {"threadId": "thread-1"})),
        ]
    )
    # Late RTP audio must arrive inside the quiescence window so one turn ends
    # exactly once, after the last chunk.
    endpoint = _FakeAudioEndpoint(output_schedule=((0.05, b"\x01\x00"), (0.05, b"\x01\x00")))
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

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
                        "active_transcript": [{"role": "user", "text": "older question"}],
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
    assert [event.text for event in events if event.type == "output_transcript_delta"] == [
        "first. ",
        "second. ",
        "third.",
    ]
    await session.close()


@pytest.mark.asyncio
async def test_normalization_queue_backpressures_and_pump_cleans_up() -> None:
    client = _Client()
    subscription = _CountingSubscription()
    client.subscription = subscription
    # No local recognizer: this test isolates queue backpressure from transcript
    # grounding, which is covered independently below.
    session = await _provider(client).open_session(
        RealtimeSessionConfig(transport_offer_sdp="v=0\r\no=offer")
    )
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
    provider_session = await _provider(provider_error_client).open_session(RealtimeSessionConfig())
    provider_events = [event async for event in provider_session.receive()]
    assert provider_events[0].type == "error"
    assert provider_events[0].recoverable is False

    dead_client = _Client(failure=RuntimeError("process exited"))
    dead_session = await _provider(dead_client).open_session(RealtimeSessionConfig())
    dead_events = [event async for event in dead_session.receive()]
    assert dead_events[0].type == "error"
    assert dead_events[0].recoverable is True
    assert "notification stream failed" in str(dead_events[0].error)
    await provider_session.close()
    await dead_session.close()


@pytest.mark.asyncio
async def test_media_endpoint_failure_cleans_parallel_thread_before_realtime() -> None:
    """A failed local offer must not leave the parallel thread start behind."""
    client = _Client()

    class _BrokenEndpoint(_FakeAudioEndpoint):
        async def create_offer(self) -> str:
            raise RuntimeError("no local WebRTC endpoint")

    provider = _provider(client, endpoint=_BrokenEndpoint())

    with pytest.raises(RuntimeError, match="no local WebRTC endpoint"):
        await provider.open_session(RealtimeSessionConfig())

    assert len(client.thread_starts) == 1
    assert client.realtime_starts == []
    assert client.stops == ["thread-1"]
    assert client.unsubscribes == ["thread-1"]
    assert provider.test_endpoint.closed is True


@pytest.mark.asyncio
async def test_offer_thread_and_transcriber_warm_overlap() -> None:
    """Independent cold-start work runs concurrently, without timing guesses."""
    offer_started = asyncio.Event()
    warm_started = asyncio.Event()
    release_parallel_work = asyncio.Event()

    class _GatedEndpoint(_FakeAudioEndpoint):
        async def create_offer(self) -> str:
            offer_started.set()
            await release_parallel_work.wait()
            return await super().create_offer()

    class _GatedTranscriber:
        async def warm(self) -> bool:
            warm_started.set()
            await release_parallel_work.wait()
            return True

        async def close(self) -> None:
            return None

    class _GatedClient(_Client):
        async def thread_start(self, **kwargs):
            await asyncio.gather(offer_started.wait(), warm_started.wait())
            release_parallel_work.set()
            return await super().thread_start(**kwargs)

    client = _GatedClient()
    provider = _provider(
        client,
        endpoint=_GatedEndpoint(),
        input_transcriber_factory=_GatedTranscriber,
    )

    session = await asyncio.wait_for(provider.open_session(RealtimeSessionConfig()), timeout=1.0)

    assert offer_started.is_set()
    assert warm_started.is_set()
    await session.close()


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


@pytest.fixture(autouse=True)
def _isolated_stun_media_path_memory():
    """The STUN media-path memory is process-global; tests must not leak it."""
    from jarvis import codex_app_server

    with codex_app_server._stun_media_path_lock:
        codex_app_server._stun_media_path_memory.clear()
    yield
    with codex_app_server._stun_media_path_lock:
        codex_app_server._stun_media_path_memory.clear()


@pytest.mark.asyncio
async def test_unconnectable_host_path_retries_with_stun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host candidates cost no gathering time and connect on an ordinary
    network; a network that needs a reflexive candidate must still work, so a
    dead media path is retried once WITH a STUN server."""
    from jarvis.realtime import webrtc_transport

    monkeypatch.setattr(webrtc_transport, "stun_ice_servers", lambda: ["stun"])
    WebRtcMediaPathUnavailable = webrtc_transport.WebRtcMediaPathUnavailable

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

    provider = CodexSubscriptionRealtimeProvider(client=client, audio_endpoint_factory=factory)

    session = await provider.open_session(RealtimeSessionConfig())

    # First attempt host-only (no servers), second with STUN.
    assert ice_configs[0] is None
    assert ice_configs[1]
    assert len(ice_configs) == 2
    await session.close()


@pytest.mark.asyncio
async def test_stun_memory_starts_with_stun_and_host_success_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network that needed STUN last time skips the doomed host-only attempt;
    a later successful host-only connect forgets the preference again."""
    from jarvis import codex_app_server
    from jarvis.realtime import webrtc_transport

    monkeypatch.setattr(webrtc_transport, "stun_ice_servers", lambda: ["stun"])

    client = _Client()
    ice_configs: list[object] = []

    def factory(ice_servers=None) -> _FakeAudioEndpoint:
        ice_configs.append(ice_servers)
        return _FakeAudioEndpoint()

    provider = CodexSubscriptionRealtimeProvider(client=client, audio_endpoint_factory=factory)

    codex_app_server.record_media_path_outcome("chatgpt-live", needed_stun=True)
    session = await provider.open_session(RealtimeSessionConfig())
    await session.close()
    # STUN went FIRST and succeeded in a single attempt.
    assert ice_configs == [["stun"]]
    # The successful STUN connect keeps the preference armed.
    assert codex_app_server.media_path_prefers_stun("chatgpt-live")

    codex_app_server.record_media_path_outcome("chatgpt-live", needed_stun=False)
    ice_configs.clear()
    session = await provider.open_session(RealtimeSessionConfig())
    await session.close()
    # Forgotten preference restores host-candidates-first.
    assert ice_configs == [None]
    assert not codex_app_server.media_path_prefers_stun("chatgpt-live")


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

    provider = CodexSubscriptionRealtimeProvider(client=client, audio_endpoint_factory=factory)

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


class _RecoveringEndpointer(_StubEndpointer):
    def __init__(self, text: str, *, ground_call: bool = True) -> None:
        super().__init__(speaking=True)
        self.text = text
        self.recovery_calls: list[tuple[bytes, int]] = []
        self._events = asyncio.Queue()
        self._events.put_nowait(InputTranscriptEvent(kind="speech_started"))
        if ground_call:
            # Under the no-unsolicited-opening policy an answer only plays
            # once a user final exists.
            self._events.put_nowait(
                InputTranscriptEvent(kind="transcript", text="Hi.", is_final=True)
            )

    async def next_event(self):  # noqa: ANN202 - test protocol
        return await self._events.get()

    async def transcribe_audio(self, pcm: bytes, *, sample_rate: int) -> str:
        self.recovery_calls.append((pcm, sample_rate))
        return self.text


class _ScriptedInputTranscriber(_StubEndpointer):
    def __init__(self, events) -> None:
        super().__init__(speaking=True)
        self._events = asyncio.Queue()
        for event in events:
            self._events.put_nowait(event)

    async def next_event(self):  # noqa: ANN202 - test protocol
        return await self._events.get()


class _GroundedThenQuietTranscriber(_ScriptedInputTranscriber):
    """Delivers one real local utterance, then reports a quiet microphone."""

    def __init__(self) -> None:
        super().__init__(
            [
                InputTranscriptEvent(kind="speech_started"),
                InputTranscriptEvent(kind="transcript", text="What is up?", is_final=True),
            ]
        )
        self._speaking = False


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
    if speaking is None:
        transcriber = None
    elif speaking:
        transcriber = _ScriptedInputTranscriber([InputTranscriptEvent(kind="speech_started")])
    else:
        transcriber = _StubEndpointer(speaking=False)
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


@pytest.mark.asyncio
async def test_orphan_response_without_a_fresh_local_utterance_is_interrupted():
    """ChatGPT-Live must not turn its own output into a second user turn.

    The live failure started a second response two milliseconds after the first
    turn completed, before any final user transcript existed.  Its server-side
    user preview then drifted to ``"sorry, i"`` while the model continued a
    made-up conversation.  A fresh local speech boundary is the authority for
    another automatic response; server VAD and captions are not.
    """
    transcriber = _ScriptedInputTranscriber(
        [
            InputTranscriptEvent(kind="speech_started"),
            InputTranscriptEvent(kind="transcript", text="Hello", is_final=True),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.02,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "Hi."},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/itemAdded",
                    {"threadId": "thread-1", "item": {"type": "response.done"}},
                ),
            ),
            (
                0.0,
                _Notification(
                    "turn/started",
                    {"threadId": "thread-1", "turn": {"id": "orphan-turn"}},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/itemAdded",
                    {
                        "threadId": "thread-1",
                        "item": {"type": "response.created"},
                    },
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {"threadId": "thread-1", "role": "user", "delta": "sorry, i"},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "text": "An invented second answer.",
                    },
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/itemAdded",
                    {
                        "threadId": "thread-1",
                        "item": {"type": "response.cancelled"},
                    },
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/itemAdded",
                    {"threadId": "thread-1", "item": {"type": "response.done"}},
                ),
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = [event async for event in session.receive()]

    assert [event.text for event in events if event.type == "output_transcript_delta"] == ["Hi."]
    assert [event.type for event in events].count("turn_complete") == 1
    assert not [event for event in events if event.type == "interrupted"]
    assert not [
        event for event in events if event.type == "input_transcript" and not event.is_final
    ]
    assert client.interrupts == [("thread-1", "orphan-turn")]
    await session.close()


@pytest.mark.asyncio
async def test_ungrounded_server_turn_aborts_an_open_response_without_a_boundary(
    monkeypatch,
):
    """A missing response boundary must not merge a self-dialogue forever.

    Live Codex v3 evidence (2026-08-02) carried no terminal response item. The
    legitimate answer therefore stayed open while silence produced a new
    server-side user caption, and every automatic reply was accepted as more
    of the original response. A RUN of ungrounded final captions must close the
    local turn and force a clean transport rebuild - a single one must not,
    because a server that transcribes with its own latency is normal and
    tearing the transport down for it costs a full handshake every turn.
    """
    monkeypatch.setattr(codex_subscription_mod, "_UNGROUNDED_RESPONSE_GRACE_S", 0.0)
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.01,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "Hi."},
                ),
            ),
            (
                0.01,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Thanks."},
                ),
            ),
            # A single late caption is survivable and must NOT tear the
            # transport down (a server that transcribes slowly is normal).
            # A RUN of them is the self-dialogue loop.
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Sure thing."},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Anything else?"},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "text": "An invented second side of the conversation.",
                    },
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        input_transcriber_factory=_GroundedThenQuietTranscriber,
    ).open_session(RealtimeSessionConfig())

    events = [event async for event in session.receive()]

    assert [event.text for event in events if event.type == "output_transcript_delta"] == ["Hi."]
    assert [event.type for event in events].count("turn_complete") == 1
    errors = [event for event in events if event.type == "error"]
    assert len(errors) == 1
    assert errors[0].recoverable is True
    assert errors[0].reconnect_advised is True
    assert "locally grounded" in str(errors[0].error)
    await session.close()


@pytest.mark.asyncio
async def test_ungrounded_turn_warning_uses_the_resolved_session_language(
    monkeypatch,
):
    """A recovery warning must not switch the turn back to English."""
    monkeypatch.setattr(codex_subscription_mod, "_UNGROUNDED_RESPONSE_GRACE_S", 0.0)
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.01,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "Hallo."},
                ),
            ),
            (
                0.01,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Danke."},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Alles klar."},
                ),
            ),
            # A run, not a single late caption: one is survivable, a run is the
            # self-dialogue loop that earns the rebuild warning.
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {
                        "threadId": "thread-1",
                        "role": "user",
                        "text": "Und weiter?",  # i18n-allow
                    },
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        input_transcriber_factory=_GroundedThenQuietTranscriber,
    ).open_session(RealtimeSessionConfig(language="de"))

    events = [event async for event in session.receive()]

    errors = [event for event in events if event.type == "error"]
    assert [event.error for event in errors] == [
        codex_subscription_mod._UNGROUNDED_TURN_MESSAGES["de"]
    ]
    await session.close()


@pytest.mark.asyncio
async def test_trusted_direct_speech_is_allowed_without_a_new_user_utterance():
    """Action readbacks intentionally create output outside a microphone turn."""
    transcriber = _StubEndpointer(speaking=False)
    client = _Client(
        [
            _Notification(
                "thread/realtime/itemAdded",
                {"threadId": "thread-1", "item": {"type": "response.created"}},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {
                    "threadId": "thread-1",
                    "role": "assistant",
                    "text": "The action is complete.",
                },
            ),
            _Notification(
                "thread/realtime/itemAdded",
                {"threadId": "thread-1", "item": {"type": "response.done"}},
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )
    await session.send_speech("The action is complete.")

    events = [event async for event in session.receive()]

    assert client.speech_appends == [("thread-1", "The action is complete.")]
    assert [event.text for event in events if event.type == "output_transcript_delta"] == [
        "The action is complete."
    ]
    assert [event.type for event in events].count("turn_complete") == 1
    await session.close()


@pytest.mark.asyncio
async def test_local_failure_before_server_done_promotes_the_late_preview():
    """Either ordering must still commit the spoken user turn."""
    transcriber = _ScriptedInputTranscriber(
        [
            InputTranscriptEvent(kind="speech_started"),
            InputTranscriptEvent(kind="transcript_failed"),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.02,
                _Notification(
                    "thread/realtime/transcript/done",
                    {
                        "threadId": "thread-1",
                        "role": "user",
                        "text": "Hello, what is up?",
                    },
                ),
            )
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = [event async for event in session.receive()]

    finals = [event for event in events if event.type == "input_transcript" and event.is_final]
    assert [event.text for event in finals] == ["Hello, what is up?"]
    await session.close()


@pytest.mark.asyncio
async def test_missing_output_transcript_is_recovered_from_provider_audio(
    monkeypatch,
) -> None:
    """Provider audio must not become a silent turn when its text is absent.

    Highest-impact live defect, 2026-08-02: a correctly recognized casual
    greeting moved LISTENING -> PROCESSING -> LISTENING while the complete
    answer was discarded as "output transcript missing". The user heard
    nothing and the product looked as though it only kept listening.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.03)
    speech = (900).to_bytes(2, "little", signed=True) * 480
    silence = b"\x00\x00" * 480
    endpoint = _FakeAudioEndpoint(output_schedule=((0.01, speech), (0.0, silence)))
    transcriber = _RecoveringEndpointer("Hi there, everything is fine.")
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.2,
                _Notification("thread/realtime/keepalive", {"threadId": "thread-1"}),
            )
        ]
    )
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    events = []
    async for event in session.receive():
        events.append(event)
        if event.type == "turn_complete":
            break

    transcript_index = next(
        index for index, event in enumerate(events) if event.type == "output_transcript_delta"
    )
    complete_index = next(
        index for index, event in enumerate(events) if event.type == "turn_complete"
    )
    assert events[transcript_index].text == "Hi there, everything is fine."
    assert transcript_index < complete_index
    assert transcriber.recovery_calls == [(speech + silence, 24_000)]
    await session.close()


@pytest.mark.asyncio
async def test_media_track_end_is_a_rebuildable_transport_error() -> None:
    endpoint = _FakeAudioEndpoint(output_schedule=((0.0, None),))
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.2,
                _Notification("thread/realtime/keepalive", {"threadId": "thread-1"}),
            )
        ]
    )
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

    event = await anext(session.receive())

    assert event.type == "error"
    assert "media track ended unexpectedly" in str(event.error)
    assert event.recoverable is True
    assert session.rebuild_on_transport_death is True
    await session.close()


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
    endpoint = _FakeAudioEndpoint(output_schedule=tuple((0.02, silence) for _ in range(25)))
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

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
    endpoint = _FakeAudioEndpoint(output_schedule=tuple((0.02, speech) for _ in range(25)))
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

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
    """ "Is this really what Codex uses?" has to be answerable from evidence."""
    caplog.set_level(logging.INFO)
    client = _Client(
        [
            _Notification(
                "thread/realtime/started",
                {"threadId": "thread-1", "version": 3},
            )
        ]
    )
    session = await _provider(client).open_session(RealtimeSessionConfig(voice="cove"))

    [event async for event in session.receive()]

    assert any(
        "negotiated protocol 3" in record.getMessage() and "cove" in record.getMessage()
        for record in caplog.records
    )
    await session.close()


@pytest.mark.asyncio
async def test_warm_transport_never_raises(monkeypatch) -> None:
    """The whole point is to move a cold start OUT of the call. A warm that
    fails may only mean the next call pays what it pays today."""
    calls = []

    async def _boom(cfg):  # noqa: ANN001 - classmethod shape
        calls.append(cfg)
        raise RuntimeError("no login")

    monkeypatch.setattr(CodexSubscriptionRealtimeProvider, "verify_activation", _boom)

    await CodexSubscriptionRealtimeProvider.warm_transport(object())

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_reply_without_any_transcript_still_ends_its_turn(monkeypatch) -> None:
    """A turn that never ends leaves Jarvis DEAF for the rest of the call.

    Half-duplex keeps the microphone shut while the assistant is speaking, so
    a missing boundary is not a cosmetic problem — the user talks and nothing
    reaches the session. The backstop therefore has to be ARMED by audible
    audio itself, not only by a transcript part that may never arrive.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.1)
    client = _Client()
    client.subscription = _keeps_stream_open()  # no transcript notification at all
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    silence = b"\x00\x00" * 480
    endpoint = _FakeAudioEndpoint(
        output_schedule=(
            *((0.02, speech) for _ in range(10)),
            *((0.02, silence) for _ in range(25)),
        )
    )
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

    completions = 0
    async with asyncio.timeout(1.5):
        async for event in session.receive():
            if event.type == "turn_complete":
                completions += 1
                break

    assert completions == 1
    await session.close()


class _ScheduledInputTranscriber(_StubEndpointer):
    """Local endpointer whose events arrive at controlled times.

    ``_ScriptedInputTranscriber`` delivers everything at once, which cannot
    express "the user speaks AFTER the far end already said something" — the
    exact ordering the grounding gate has to survive.
    """

    def __init__(self, scheduled_events, *, speaking: bool = True) -> None:
        super().__init__(speaking=speaking)
        self._scheduled = list(scheduled_events)

    async def next_event(self):  # noqa: ANN202 - test protocol
        if not self._scheduled:
            await asyncio.sleep(3600)
        delay_s, event = self._scheduled.pop(0)
        if delay_s:
            await asyncio.sleep(delay_s)
        return event


async def _collect_until(session, *, stop_after: int, kind: str, timeout_s: float):
    """Drain the session until ``stop_after`` events of ``kind`` were seen."""
    events = []
    seen = 0
    try:
        async with asyncio.timeout(timeout_s):
            async for event in session.receive():
                events.append(event)
                if event.type == kind:
                    seen += 1
                    if seen >= stop_after:
                        break
    except TimeoutError:
        pass
    return events


@pytest.mark.asyncio
async def test_provider_speech_start_does_not_duplicate_local_boundary() -> None:
    """A late server VAD edge is the same utterance, not a new barge-in.

    ChatGPT-Live commonly sends it after the locally grounded final, while the
    answer is already opening. Forwarding both boundaries cancels that answer
    and strands the following response generation behind the interrupt barrier.
    """
    transcriber = _ScheduledInputTranscriber(
        [
            (0.01, InputTranscriptEvent(kind="speech_started")),
            (
                0.0,
                InputTranscriptEvent(kind="transcript", text="Hello", is_final=True),
            ),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.04,
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
            ),
            (
                0.02,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "Hi there.",
                    },
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    events = await _collect_until(
        session,
        stop_after=1,
        kind="output_transcript_delta",
        timeout_s=1.0,
    )

    assert [event.type for event in events].count("speech_started") == 1
    assert [event.text for event in events if event.type == "output_transcript_delta"] == [
        "Hi there."
    ]
    assert session.diagnostics().get("duplicate_provider_speech_starts_suppressed", 0) == 1
    await session.close()


@pytest.mark.asyncio
async def test_own_cancel_ack_keeps_the_speech_turn_answerable() -> None:
    """A delayed cancel acknowledgement is not a second user barge-in.

    Live protocol v3 delivered ``response.cancelled`` after the local FINAL
    had already re-authorized the response. Treating that acknowledgement as
    an external interrupt both split the recorded turn and consumed the only
    entitlement for the answer, leaving a healthy session permanently mute.
    """
    transcriber = _ScheduledInputTranscriber(
        [
            (0.02, InputTranscriptEvent(kind="speech_started")),
            (0.0, InputTranscriptEvent(kind="transcript", text="Hello", is_final=True)),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "Unsolicited opener.",
                    },
                ),
            ),
            (
                0.04,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "Answer head. ",
                    },
                ),
            ),
            (
                0.01,
                _Notification(
                    "thread/realtime/itemAdded",
                    {
                        "threadId": "thread-1",
                        "item": {"type": "response.cancelled"},
                    },
                ),
            ),
            (
                0.01,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "Answer tail.",
                    },
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    events = []
    async with asyncio.timeout(1.0):
        async for event in session.receive():
            events.append(event)
            if event.type == "speech_started":
                await session.interrupt()
            if event.type == "output_transcript_delta" and event.text == "Answer tail.":
                break

    assert [event.text for event in events if event.type == "output_transcript_delta"] == [
        "Answer head. ",
        "Answer tail.",
    ]
    cancelled = [event for event in events if event.type == "interrupted"]
    assert len(cancelled) == 1
    assert cancelled[0].self_initiated is True
    assert session.diagnostics().get("self_initiated_cancellations", 0) == 1
    await session.close()


@pytest.mark.asyncio
async def test_a_refused_response_does_not_deafen_the_next_real_turn() -> None:
    """AD-1: one refusal must not decide the rest of the call.

    The gate caches its verdict for the whole response it refused, and a
    ChatGPT-Live response has no reliable end marker. A single ungrounded
    response therefore used to keep ``response_open`` true forever: every
    later frame inherited that one "no", so the session stayed connected,
    kept listening, and never answered again. A locally energy-grounded
    utterance is the one signal the far end cannot fake, so it always
    reopens the gate.
    """
    transcriber = _ScheduledInputTranscriber(
        [
            (0.05, InputTranscriptEvent(kind="speech_started")),
            (0.0, InputTranscriptEvent(kind="transcript", text="Hello", is_final=True)),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            # Arrives before any local speech: correctly refused.
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "Echo."},
                ),
            ),
            # Arrives after the user really spoke: must be delivered.
            (
                0.15,
                _Notification(
                    "thread/realtime/transcript/done",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "text": "The real answer.",
                    },
                ),
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = [event async for event in session.receive()]

    assert [event.text for event in events if event.type == "output_transcript_delta"] == [
        "The real answer."
    ]
    await session.close()


@pytest.mark.asyncio
async def test_a_stale_refusal_is_reconsidered_and_still_refuses_a_self_echo(
    monkeypatch,
) -> None:
    """Reopening the gate must not be a way in for the echo it refused.

    The refusal is reconsidered so it cannot latch, but reconsidering means
    re-deriving the verdict from scratch — with no fresh local utterance and
    no trusted injection the answer is still "no".
    """
    monkeypatch.setattr(codex_subscription_mod, "_REJECTED_RESPONSE_MAX_S", 0.0)
    client = _Client(
        [
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "One."},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "Two."},
            ),
        ]
    )
    session = await _provider(
        client, input_transcriber_factory=lambda: _StubEndpointer(speaking=False)
    ).open_session(RealtimeSessionConfig())

    events = [event async for event in session.receive()]

    assert not [event for event in events if event.type == "output_transcript_delta"]
    await session.close()


@pytest.mark.asyncio
async def test_a_pause_inside_one_reply_does_not_cut_off_its_remainder(
    monkeypatch,
) -> None:
    """AD-2: the local backstop must not retire the user's entitlement.

    The backstop closes the LOCAL turn on silence; it proves nothing about the
    provider's response. Spending the utterance there made the rest of the
    same answer look like a brand-new ungrounded response, which the gate then
    refused — one ordinary thinking pause and the reply was cut off mid-way.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 0.05)
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(
        # The gap is far longer than the backstop: a real in-reply pause.
        output_schedule=((0.02, speech), (0.30, speech))
    )
    transcriber = _ScheduledInputTranscriber(
        [
            (0.0, InputTranscriptEvent(kind="speech_started")),
            # Grounded call: the first answer needs a user final now.
            (
                0.0,
                InputTranscriptEvent(kind="transcript", text="Hello there.", is_final=True),
            ),
        ]
    )
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    events = await _collect_until(session, stop_after=2, kind="audio_delta", timeout_s=1.5)

    assert [event.type for event in events].count("audio_delta") == 2
    # The backstop still delivered its boundary between the two halves.
    assert [event.type for event in events].count("turn_complete") == 1
    await session.close()


@pytest.mark.asyncio
async def test_a_completed_turn_still_refuses_the_next_ungrounded_response() -> None:
    """The self-dialogue guard, pinned against the continuation allowance.

    Letting the rest of ONE answer through must not let a SECOND answer
    through. Protocol proof that the response ended (here the terminal item)
    retires the entitlement immediately, so the invented follow-up is refused
    even though the far end is still producing output milliseconds later.
    """
    transcriber = _ScriptedInputTranscriber(
        [
            InputTranscriptEvent(kind="speech_started"),
            # Grounded call: the first answer needs a user final now.
            InputTranscriptEvent(kind="transcript", text="Hello there.", is_final=True),
        ]
    )
    client = _Client(
        [
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "Answer one."},
            ),
            _Notification(
                "thread/realtime/itemAdded",
                {"threadId": "thread-1", "item": {"type": "response.done"}},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {
                    "threadId": "thread-1",
                    "role": "assistant",
                    "text": "An invented second answer.",
                },
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = [event async for event in session.receive()]

    assert [event.text for event in events if event.type == "output_transcript_delta"] == [
        "Answer one."
    ]
    assert [event.type for event in events].count("turn_complete") == 1
    await session.close()


@pytest.mark.asyncio
async def test_an_invented_user_caption_retires_the_entitlement() -> None:
    """The other half of the self-dialogue guard, without a terminal item.

    ChatGPT-Live announced no response boundary in the live evidence, so the
    marker that the model considers its turn over is the user caption it
    invents next. That caption ends the entitlement, and the answer it writes
    for its own invented turn is refused.
    """
    transcriber = _ScriptedInputTranscriber(
        [
            InputTranscriptEvent(kind="speech_started"),
            # Grounded call: the first answer needs a user final now.
            InputTranscriptEvent(kind="transcript", text="Hello there.", is_final=True),
        ]
    )
    transcriber._speaking = False  # the microphone is quiet from here on
    client = _Client(
        [
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "Answer one."},
            ),
            _Notification(
                "thread/realtime/transcript/delta",
                {"threadId": "thread-1", "role": "user", "delta": "thanks, i"},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {
                    "threadId": "thread-1",
                    "role": "assistant",
                    "text": "An invented second answer.",
                },
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = [event async for event in session.receive()]

    assert [event.text for event in events if event.type == "output_transcript_delta"] == [
        "Answer one."
    ]
    await session.close()


@pytest.mark.asyncio
async def test_interrupt_drops_the_remainder_without_any_codex_turn_id(
    monkeypatch,
) -> None:
    """AD-3: barge-in must work on an ordinary ChatGPT-Live response.

    ``turn/interrupt`` addresses an app-server TURN and a realtime response
    never announces one, so the remote call had nothing to interrupt and the
    assistant simply kept talking into an already-open microphone. The local
    half is the one that always works.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_QUIESCENCE_S", 5.0)
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(output_schedule=((0.02, speech), (0.15, speech), (0.05, speech)))
    transcriber = _ScheduledInputTranscriber(
        [
            (0.0, InputTranscriptEvent(kind="speech_started")),
            # Grounded call: the first answer needs a user final now.
            (
                0.0,
                InputTranscriptEvent(kind="transcript", text="Hello there.", is_final=True),
            ),
        ]
    )
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    stream = session.receive()
    async with asyncio.timeout(1.5):
        async for event in stream:
            if event.type == "audio_delta":
                break

    await session.interrupt()

    later_audio = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(0.6):
            async for event in stream:
                if event.type == "audio_delta":
                    later_audio.append(event)

    # No Codex turn was ever announced, so the remote interrupt had no target.
    assert client.interrupts == []
    assert later_audio == []
    await session.close()


@pytest.mark.asyncio
async def test_late_provider_transcript_is_shadow_recovered_for_the_gate(
    monkeypatch,
) -> None:
    """Audible audio with no provider transcript earns a SHADOW delta.

    ChatGPT-Live's transcripts can lag their audio by seconds; the session's
    scrub gate holds all of it fail-closed until SOME transcript arrives
    (live 2026-08-05 20:42: the reply's first audio sat 7.4 s in the opening
    hold). The local recognizer recovers vetting text early; the shadow flag
    keeps it out of the user-visible transcript.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_EARLY_RECOVERY_AFTER_S", 0.05)
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_EARLY_RECOVERY_RETRY_S", 0.01)
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(
        output_schedule=(
            (0.02, speech),
            (0.05, speech),
            (0.05, speech),
            (0.05, speech),
            (0.05, speech),
        )
    )
    transcriber = _RecoveringEndpointer("Here is the answer.")
    # The user's utterance must CLOSE (final transcript) before the shadow
    # attempt may borrow the shared recognizer (review R2).
    transcriber._events.put_nowait(
        InputTranscriptEvent(kind="transcript", text="Hi", is_final=True)
    )
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    shadow = None
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(1.5):
            async for event in session.receive():
                if event.type == "output_transcript_delta" and getattr(event, "shadow", False):
                    shadow = event
                    break

    assert shadow is not None
    assert shadow.text == "Here is the answer."
    assert transcriber.recovery_calls
    await session.close()


@pytest.mark.asyncio
async def test_slow_shadow_recovery_never_stalls_the_receive_pump(
    monkeypatch,
) -> None:
    """A slow recognizer must not block audio delivery (review R1, measured).

    The first inline implementation held the whole receive loop for the
    recognizer's own time bound — audio stalled and the user's speech edge
    arrived seconds late. The recovery now runs as a background task.
    """
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_EARLY_RECOVERY_AFTER_S", 0.05)
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_EARLY_RECOVERY_RETRY_S", 0.01)

    class _SlowRecoveringEndpointer(_RecoveringEndpointer):
        async def transcribe_audio(self, pcm: bytes, *, sample_rate: int) -> str:
            await asyncio.sleep(0.4)
            return await super().transcribe_audio(pcm, sample_rate=sample_rate)

    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(output_schedule=tuple((0.04, speech) for _ in range(14)))
    transcriber = _SlowRecoveringEndpointer("Here is the answer.")
    transcriber._events.put_nowait(
        InputTranscriptEvent(kind="transcript", text="Hi", is_final=True)
    )
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    audio_before_shadow = 0
    shadow_seen = False
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(2.0):
            async for event in session.receive():
                if event.type == "audio_delta" and not shadow_seen:
                    audio_before_shadow += 1
                if event.type == "output_transcript_delta" and getattr(event, "shadow", False):
                    shadow_seen = True
                    break

    assert shadow_seen
    # While the recognizer slept 0.4 s, ~10 further 40 ms chunks were due;
    # a blocking implementation delivers almost none of them before the
    # shadow event.
    assert audio_before_shadow >= 8
    await session.close()


@pytest.mark.asyncio
async def test_shadow_recovery_yields_to_an_open_user_utterance(
    monkeypatch,
) -> None:
    """The microphone owns the shared recognizer mid-utterance (review R2)."""
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_EARLY_RECOVERY_AFTER_S", 0.05)
    monkeypatch.setattr(codex_subscription_mod, "_OUTPUT_EARLY_RECOVERY_RETRY_S", 0.01)
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(output_schedule=tuple((0.04, speech) for _ in range(8)))
    # speech_started with no final: the utterance stays open for the whole
    # window, so no shadow attempt may run (and under the
    # no-unsolicited-opening policy the response never plays either).
    transcriber = _RecoveringEndpointer("Must never be used.", ground_call=False)
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(0.8):
            async for event in session.receive():
                assert not (
                    event.type == "output_transcript_delta" and getattr(event, "shadow", False)
                )

    assert transcriber.recovery_calls == []
    await session.close()


@pytest.mark.asyncio
async def test_interrupt_retires_an_entitlement_the_response_never_used(
    monkeypatch,
) -> None:
    """A delegation interrupt must beat the response's FIRST frame too.

    When Jarvis takes a turn deterministically it interrupts before the far
    end has begun streaming. Closing only the OPEN response left the spoken
    utterance's entitlement standing, so the "retired" native answer simply
    started a moment later and played over the trusted delegate reply (live
    2026-08-04: an unrelated English fragment replaced the computed weather
    answer).
    """
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(output_schedule=((0.35, speech), (0.02, speech)))
    transcriber = _ScheduledInputTranscriber(
        [
            (0.0, InputTranscriptEvent(kind="speech_started")),
            (
                0.02,
                InputTranscriptEvent(kind="transcript", text="Wetter morgen?", is_final=True),
            ),
        ]
    )
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    stream = session.receive()
    async with asyncio.timeout(1.5):
        async for event in stream:
            if event.type == "input_transcript" and event.is_final:
                break

    await session.interrupt(retire_input_entitlement=True)

    audio_after = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(0.8):
            async for event in stream:
                if event.type == "audio_delta":
                    audio_after.append(event)

    assert audio_after == []
    await session.close()


@pytest.mark.asyncio
async def test_barge_in_never_consumes_the_new_utterances_entitlement() -> None:
    """A plain barge-in interrupt must keep the NEW question answerable.

    At the moment ``_barge_in`` fires, the local input generation already
    belongs to the user's new utterance. Retiring it there consumed that
    question's one response entitlement, so the assistant fell permanently
    silent after every barge-in (independent review 2026-08-05: 3 frames
    before the change, 0 after). Only a delegation may retire the
    entitlement, via ``retire_input_entitlement=True``.
    """
    speech = (1000).to_bytes(2, "little", signed=True) * 480
    endpoint = _FakeAudioEndpoint(output_schedule=((0.5, speech), (0.02, speech)))
    transcriber = _ScheduledInputTranscriber(
        [
            (0.0, InputTranscriptEvent(kind="speech_started")),
            (
                0.02,
                InputTranscriptEvent(kind="transcript", text="Frage eins", is_final=True),
            ),
            (0.05, InputTranscriptEvent(kind="speech_started")),
            (
                0.02,
                InputTranscriptEvent(kind="transcript", text="Warte, anders", is_final=True),
            ),
        ]
    )
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    stream = session.receive()
    finals = 0
    async with asyncio.timeout(1.5):
        async for event in stream:
            if event.type == "input_transcript" and event.is_final:
                finals += 1
                if finals == 2:
                    break

    await session.interrupt()

    audio_after = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(1.0):
            async for event in stream:
                if event.type == "audio_delta":
                    audio_after.append(event)
                    break

    assert audio_after, "the far end's answer to the barge-in utterance must stay audible"
    await session.close()


@pytest.mark.asyncio
async def test_post_interrupt_captions_do_not_feed_the_rebuild_counter(
    monkeypatch,
) -> None:
    """Echo captions caused by OUR OWN cut must not tear the transport down.

    The far end predictably reacts to a local interrupt with captions of its
    own truncated speech. Counting those toward the self-dialogue rebuild
    threshold turned every delegation into a rebuild storm (live 2026-08-04:
    three transport rebuilds inside one 43 s call).
    """
    monkeypatch.setattr(codex_subscription_mod, "_UNGROUNDED_RESPONSE_GRACE_S", 0.0)
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.01,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "assistant", "text": "Hi."},
                ),
            ),
            (
                0.02,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Thanks."},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Sure thing."},
                ),
            ),
            (
                0.0,
                _Notification(
                    "thread/realtime/transcript/done",
                    {"threadId": "thread-1", "role": "user", "text": "Anything else?"},
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        input_transcriber_factory=_GroundedThenQuietTranscriber,
    ).open_session(RealtimeSessionConfig())

    stream = session.receive()
    async with asyncio.timeout(1.5):
        async for event in stream:
            if event.type == "output_transcript_delta":
                break

    await session.interrupt()

    events = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(0.8):
            async for event in stream:
                events.append(event)

    rebuild_errors = [
        event
        for event in events
        if event.type == "error" and getattr(event, "reconnect_advised", False)
    ]
    assert rebuild_errors == []
    await session.close()


def _self_echo_client(rounds: int) -> _Client:
    """A far end answering itself: each refused turn captions its own words.

    One ungrounded user caption closes the open response, the assistant text
    that follows opens a new one with nothing local behind it — a refusal —
    and the caption after that is the far end transcribing the very words we
    just cut. That is the shape of the live 17:41 loop.
    """
    events = [
        _Notification(
            "thread/realtime/transcript/delta",
            {"threadId": "thread-1", "role": "assistant", "delta": "Hi."},
        )
    ]
    for index in range(rounds):
        events.append(
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "user", "text": f"echo {index}"},
            )
        )
        events.append(
            _Notification(
                "thread/realtime/transcript/delta",
                {
                    "threadId": "thread-1",
                    "role": "assistant",
                    "delta": f"invented turn {index}",
                },
            )
        )
    return _Client(events)


async def _drain(session) -> list:
    events = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(2.0):
            async for event in session.receive():
                events.append(event)
    return events


@pytest.mark.asyncio
async def test_a_refusals_own_echo_captions_never_tear_the_transport_down(
    monkeypatch,
) -> None:
    """BUG-124: refusing a turn is a LOCAL interrupt and must arm the grace.

    Live 2026-08-06 17:41: every refusal cut the far end, the far end
    captioned its own truncated words back as a user turn — the caption was
    the first words of the answer just cut — and two of those captions
    rebuilt the transport. Twice in six seconds, with the loop back each time.
    """
    monkeypatch.setattr(codex_subscription_mod, "_UNGROUNDED_RESPONSE_GRACE_S", 0.0)
    # High enough that only the caption counter could fire here: three rounds
    # deliver three ungrounded final captions against a threshold of two, so
    # this test fails outright unless the refusals arm the grace window.
    monkeypatch.setattr(codex_subscription_mod, "_REFUSALS_BEFORE_REBUILD", 99)
    session = await _provider(
        _self_echo_client(rounds=3),
        input_transcriber_factory=_GroundedThenQuietTranscriber,
    ).open_session(RealtimeSessionConfig())

    events = await _drain(session)

    assert [
        event
        for event in events
        if event.type == "error" and getattr(event, "reconnect_advised", False)
    ] == []
    await session.close()


@pytest.mark.asyncio
async def test_a_run_of_refusals_is_what_proves_the_self_dialogue_loop(
    monkeypatch,
) -> None:
    """The honest signal: refusals, which our own reaction cannot manufacture.

    With the post-interrupt grace now covering refusals, the caption counter
    can no longer see a storm at all — so the storm needs its own detector, or
    a call would refuse every answer forever in silence (the 17:39 call died
    exactly that way, and only an app restart recovered it).
    """
    monkeypatch.setattr(codex_subscription_mod, "_UNGROUNDED_RESPONSE_GRACE_S", 0.0)
    monkeypatch.setattr(codex_subscription_mod, "_REFUSALS_BEFORE_REBUILD", 3)
    client = _self_echo_client(rounds=6)
    session = await _provider(
        client,
        input_transcriber_factory=_GroundedThenQuietTranscriber,
    ).open_session(RealtimeSessionConfig())

    events = await _drain(session)

    rebuild_errors = [
        event
        for event in events
        if event.type == "error" and getattr(event, "reconnect_advised", False)
    ]
    assert len(rebuild_errors) == 1
    assert rebuild_errors[0].recoverable is True
    assert events[-1].type == "turn_complete"
    # Only the FIRST refusal of the run interrupts: cutting a turn is what
    # makes ChatGPT-Live start the next one, so a storm is left to run out.
    assert len(client.interrupts) <= 1
    await session.close()


@pytest.mark.asyncio
async def test_truncate_reports_the_played_position_to_the_model() -> None:
    """AD-3: the model must learn how much of its answer was actually heard.

    ChatGPT-Live has no truncate client event, so the position travels as an
    inert developer note. Without it the model believes the whole answer was
    heard and grounds the next turn in words the user never got.
    """
    client = _Client()
    session = await _provider(client).open_session(RealtimeSessionConfig())
    # Opening the session pins the reply language, which is itself a developer
    # write; count from there so this test measures only what truncate adds.
    baseline = len(client.text_appends)

    # Nothing was interrupted yet: a stray truncate must not write anything.
    await session.truncate(audio_end_ms=900)
    assert len(client.text_appends) == baseline

    await session.interrupt()
    await session.truncate(audio_end_ms=900)

    assert len(client.text_appends) == baseline + 1
    thread_id, text, role = client.text_appends[-1]
    assert thread_id == "thread-1"
    assert role == "developer"
    assert "900 ms" in text
    # One note per interrupt, never a repeat on the next boundary.
    await session.truncate(audio_end_ms=900)
    assert len(client.text_appends) == baseline + 1
    await session.close()


@pytest.mark.asyncio
async def test_startup_context_never_authorizes_a_response() -> None:
    """Atomic startup configuration must not buy the model a spoken turn."""
    client = _Client(
        [
            _Notification(
                "thread/realtime/itemAdded",
                {"threadId": "thread-1", "item": {"type": "response.created"}},
            ),
            _Notification(
                "thread/realtime/transcript/done",
                {
                    "threadId": "thread-1",
                    "role": "assistant",
                    "text": "Understood.",
                },
            ),
            _Notification(
                "thread/realtime/itemAdded",
                {"threadId": "thread-1", "item": {"type": "response.done"}},
            ),
        ]
    )
    session = await _provider(
        client, input_transcriber_factory=lambda: _StubEndpointer(speaking=False)
    ).open_session(
        RealtimeSessionConfig(
            instructions="You are Nova, the user's own assistant.",
            history=({"role": "user", "text": "Earlier question"},),
            language_is_pinned=True,
        )
    )

    assert client.text_appends == []
    _thread_id, start = client.realtime_starts[0]
    assert "You are Nova" in start["trusted_prompt"]
    assert codex_subscription_mod._language_pin_text("en") in start["trusted_prompt"]
    assert start["initial_items"] == [{"role": "user", "text": "Earlier question"}]

    events = [event async for event in session.receive()]

    # No microphone energy stands behind this answer, and a configuration
    # write is not consent to speak - so it is refused, exactly like any other
    # ungrounded turn.
    assert [event.text for event in events if event.type == "output_transcript_delta"] == []
    await session.close()


@pytest.mark.asyncio
async def test_the_language_pin_never_authorizes_a_response() -> None:
    """Changing the reply language is configuration, not a request to speak.

    The pin travels as the same ``appendText`` write as an announcement, so it
    is the one place where a permit would be easiest to grant by accident -
    and it would hand the model an authorized ungrounded turn on every
    language change.
    """
    client = _Client(
        [
            _Notification(
                "thread/realtime/transcript/done",
                {"threadId": "thread-1", "role": "assistant", "text": "Verstanden."},
            ),
        ]
    )
    session = await _provider(
        client, input_transcriber_factory=lambda: _StubEndpointer(speaking=False)
    ).open_session(RealtimeSessionConfig())
    await session.update_session(language="de")

    events = [event async for event in session.receive()]

    assert [event.text for event in events if event.type == "output_transcript_delta"] == []
    await session.close()


@pytest.mark.asyncio
async def test_a_barge_splice_is_sequenced_behind_a_local_boundary() -> None:
    """Live 2026-08-06 17:39/17:41: a cut answer's replacement opened 15-140 ms
    after the previous response's audio and spliced RAW into the one playback
    stream (no boundary between them - the barge closes silently). The
    adapter now closes the spliced-over response with a local turn boundary
    BEFORE the new response's text/audio flows, and counts the sequencing so
    the postmortem can prove it happened.
    """
    transcriber = _ScheduledInputTranscriber(
        [
            (0.02, InputTranscriptEvent(kind="speech_started")),
            (
                0.0,
                InputTranscriptEvent(kind="transcript", text="Hello", is_final=True),
            ),
            # The interrupting user's own follow-up grounds response 2.
            (0.25, InputTranscriptEvent(kind="speech_started")),
            (
                0.0,
                InputTranscriptEvent(kind="transcript", text="And again", is_final=True),
            ),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.1,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "First answer",
                    },
                ),
            ),
            (
                0.5,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "Second answer",
                    },
                ),
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = []
    async with asyncio.timeout(5.0):
        async for event in session.receive():
            events.append(event)
            text = event.text or ""
            if event.type == "output_transcript_delta" and "First" in text:
                # The user talks over the answer: the barge-in cuts response 1
                # WITHOUT any boundary event of its own.
                await session.interrupt()
            if event.type == "output_transcript_delta" and "Second" in text:
                break

    deltas = [i for i, event in enumerate(events) if event.type == "output_transcript_delta"]
    first_idx = next(
        i
        for i, event in enumerate(events)
        if event.type == "output_transcript_delta" and "First" in (event.text or "")
    )
    second_idx = next(
        i
        for i, event in enumerate(events)
        if event.type == "output_transcript_delta" and "Second" in (event.text or "")
    )
    between = [event.type for event in events[first_idx:second_idx]]
    assert "turn_complete" in between, (
        "the spliced-over response must close behind a local boundary before "
        f"the new one speaks; saw {between} (deltas at {deltas})"
    )
    assert session.diagnostics().get("sequenced_boundaries", 0) == 1
    await session.close()


@pytest.mark.asyncio
async def test_the_standing_directive_is_reasserted_on_every_delivery() -> None:
    """A rule stated once at open does not hold on this channel (three live
    calls, 2026-08-05; probe round 1, 2026-08-06), while the per-turn
    language pin demonstrably does - repetition is what makes a rule real
    here. The standing directive must therefore ride EVERY delivery, exactly
    once per payload, even when nothing else changed."""
    client = _Client()
    session = await _provider(client).open_session(RealtimeSessionConfig())
    baseline = len(client.text_appends)

    rule = "SPEAK AS ONE VOICE ONLY."
    await session.update_session(instructions="PERSONA BLOCK", standing_directive=rule)
    await session.update_session(instructions="PERSONA BLOCK", standing_directive=rule)

    payloads = [text for _, text, _ in client.text_appends[baseline:]]
    assert len(payloads) == 2, "an unchanged turn still delivers the rule"
    for payload in payloads:
        assert payload.count(rule) == 1, payload
    await session.close()


@pytest.mark.asyncio
async def test_an_unsolicited_opening_never_plays_on_a_grounded_host() -> None:
    """Maintainer live test 2026-08-08: the bounded-greeting compromise lost —
    what played was the model echoing the user's own words and acknowledging
    itself ("geht ab? ... Okay."). Policy now mirrors the zero-role-play
    control adapter: before the call's first user FINAL, nothing unsolicited
    plays at all; the answer the question earns flows right after the final
    (the refused-open response is re-judged immediately, never after the
    stale window)."""
    loud = (1000).to_bytes(2, "little", signed=True) * 480
    transcriber = _ScheduledInputTranscriber(
        [
            (0.02, InputTranscriptEvent(kind="speech_started")),
            (
                0.55,
                InputTranscriptEvent(kind="transcript", text="What is up?", is_final=True),
            ),
        ]
    )
    endpoint = _FakeAudioEndpoint(
        output_schedule=[
            # The server's greeting monologue, streaming BEFORE any final.
            (0.05, loud),
            (0.10, loud),
            (0.10, loud),
            (0.15, loud),
            # ...and the frames it keeps sending after the final landed.
            (0.30, loud),
            (0.05, loud),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.8,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "The real answer.",
                    },
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    events = []
    final_seen = False
    audio_before_final = 0
    audio_after_final = 0
    async with asyncio.timeout(5.0):
        async for event in session.receive():
            events.append(event)
            if event.type == "input_transcript" and event.is_final:
                final_seen = True
            elif event.type == "audio_delta":
                if final_seen:
                    audio_after_final += 1
                else:
                    audio_before_final += 1
            if event.type == "output_transcript_delta" and "real answer" in (event.text or ""):
                break

    assert audio_before_final == 0, (
        "an unsolicited opening reached the surface before the first final"
    )
    assert audio_after_final > 0, "the re-judged response after the final must play"
    assert session.diagnostics().get("opening_responses_bounded", 0) == 0, (
        "grounded hosts refuse the opener outright; the bound is the recognizer-less fallback only"
    )
    await session.close()


@pytest.mark.asyncio
async def test_a_final_rejudges_the_open_refused_response_immediately() -> None:
    """The far end often starts answering before the local final lands. That
    response is (correctly) refused while ungrounded - but once the final
    arrives, waiting out the stale-refusal window would eat the answer. The
    final closes the refusal on the spot, and the next frame is granted."""
    loud = (1000).to_bytes(2, "little", signed=True) * 480
    transcriber = _ScheduledInputTranscriber(
        [
            (0.02, InputTranscriptEvent(kind="speech_started")),
            (
                0.25,
                InputTranscriptEvent(kind="transcript", text="Quick one?", is_final=True),
            ),
        ]
    )
    endpoint = _FakeAudioEndpoint(
        output_schedule=[
            (0.05, loud),  # pre-final: refused (opens the refused response)
            (0.35, loud),  # post-final: must be re-judged and granted
            (0.05, loud),
        ]
    )
    client = _Client()
    # Keep the notification stream OPEN past the collection window: an
    # exhausted subscription ends the stream and takes the receive loop
    # with it.
    client.subscription = _ScheduledSubscription(
        [
            (
                5.0,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {"threadId": "thread-1", "role": "assistant", "delta": "."},
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    events = await _collect_until(session, stop_after=2, kind="audio_delta", timeout_s=2.0)

    audio_events = [e for e in events if e.type == "audio_delta"]
    assert len(audio_events) == 2, (
        "the post-final frames must flow without waiting out the "
        f"stale-refusal window; saw {len(audio_events)}"
    )
    await session.close()


@pytest.mark.asyncio
async def test_a_late_server_user_caption_does_not_split_the_active_answer() -> None:
    """A second transcript source is not a second response boundary.

    Live 2026-08-09 20:47: the locally grounded final opened the real answer,
    then ChatGPT-Live's slower caption for the same microphone audio arrived
    while that answer was streaming. Closing the response on that caption
    fabricated a new response identity for the next PCM frame, refused the
    rest as ungrounded, and left the user waiting 15 seconds for no audio.
    """
    loud = (1000).to_bytes(2, "little", signed=True) * 480
    transcriber = _ScheduledInputTranscriber(
        [
            (0.01, InputTranscriptEvent(kind="speech_started")),
            (
                0.02,
                InputTranscriptEvent(
                    kind="transcript",
                    text="What is up?",
                    is_final=True,
                ),
            ),
        ]
    )
    endpoint = _FakeAudioEndpoint(
        output_schedule=[
            (0.10, loud),
            (0.30, loud),
            (0.10, loud),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.15,
                _Notification(
                    "thread/realtime/transcript/done",
                    {
                        "threadId": "thread-1",
                        "role": "user",
                        "text": "What is up?",
                    },
                ),
            ),
            (
                0.40,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "The complete answer.",
                    },
                ),
            ),
        ]
    )
    session = await _provider(
        client,
        endpoint=endpoint,
        input_transcriber_factory=lambda: transcriber,
    ).open_session(RealtimeSessionConfig())

    events = await _collect_until(
        session,
        stop_after=1,
        kind="output_transcript_delta",
        timeout_s=2.0,
    )

    audio = [event for event in events if event.type == "audio_delta"]
    response_ids = {
        event.provider_turn_id
        for event in events
        if event.type in {"audio_delta", "output_transcript_delta"}
    }
    assert len(audio) == 3, "the late duplicate caption must not cut the answer"
    assert len(response_ids) == 1
    assert session.diagnostics().get("response_splices", 0) == 0
    await session.close()


@pytest.mark.asyncio
async def test_a_discarded_utterance_keeps_its_response_window(
    monkeypatch,
) -> None:
    """The far end hears a 200 ms "hm?" whatever the local endpointer decides,
    and it answers. Consuming the generation at the discard instant refused
    that answer and cut it (the BUG-124 amplifier); within the grace it now
    plays - and after the grace an unanswered window retires."""
    transcriber = _ScheduledInputTranscriber(
        [
            # Priming exchange: the call opening answers no bare cough under
            # the no-unsolicited policy, so the discard under test is a
            # MID-CALL event like the live BUG-124 amplifier was.
            (0.01, InputTranscriptEvent(kind="speech_started")),
            (
                0.0,
                InputTranscriptEvent(kind="transcript", text="Hello there.", is_final=True),
            ),
            (0.05, InputTranscriptEvent(kind="speech_started")),
            (
                0.01,
                InputTranscriptEvent(kind="speech_discarded", voiced_ms=200),
            ),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.2,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "Did you say something?",
                    },
                ),
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = await _collect_until(
        session, stop_after=1, kind="output_transcript_delta", timeout_s=2.0
    )
    delivered = [event.text for event in events if event.type == "output_transcript_delta"]
    assert delivered == ["Did you say something?"], (
        "the answer to a heard-but-discarded sound must play inside the grace"
    )
    await session.close()


@pytest.mark.asyncio
async def test_an_unanswered_discard_window_expires(monkeypatch) -> None:
    monkeypatch.setattr(codex_subscription_mod, "_DISCARDED_UTTERANCE_GRACE_S", 0.05)
    transcriber = _ScheduledInputTranscriber(
        [
            # Priming exchange: the call opening answers no bare cough under
            # the no-unsolicited policy, so the discard under test is a
            # MID-CALL event like the live BUG-124 amplifier was.
            (0.01, InputTranscriptEvent(kind="speech_started")),
            (
                0.0,
                InputTranscriptEvent(kind="transcript", text="Hello there.", is_final=True),
            ),
            (0.05, InputTranscriptEvent(kind="speech_started")),
            (
                0.01,
                InputTranscriptEvent(kind="speech_discarded", voiced_ms=200),
            ),
        ]
    )
    client = _Client()
    client.subscription = _ScheduledSubscription(
        [
            (
                0.4,
                _Notification(
                    "thread/realtime/transcript/delta",
                    {
                        "threadId": "thread-1",
                        "role": "assistant",
                        "delta": "A late invented answer.",
                    },
                ),
            ),
        ]
    )
    session = await _provider(client, input_transcriber_factory=lambda: transcriber).open_session(
        RealtimeSessionConfig()
    )

    events = await _collect_until(
        session, stop_after=1, kind="output_transcript_delta", timeout_s=1.5
    )
    delivered = [event.text for event in events if event.type == "output_transcript_delta"]
    assert delivered == [], "past the grace a discarded utterance grounds nothing"
    await session.close()


def test_readback_render_budget_is_declared() -> None:
    """A delegate readback on this transport is spoken by ChatGPT-Live, whose
    audio lags its own start (7.4 s scrub-hold measured 2026-08-05). Without a
    declared budget the session's 2.5 s hosted floor governed, so the surface
    fallback took delegated answers in the wrong voice or withheld them
    entirely (AP-21: a declared capability, never a provider-name check)."""
    assert CodexSubscriptionRealtimeProvider.readback_render_budget_s == 12.0
    assert CodexSubscriptionRealtimeProvider.readback_render_budget_s > 2.5, (
        "the declared budget must beat the shared hosted floor"
    )


class _DyingTranscriber(_StubEndpointer):
    """Local recognizer whose event stream dies on first read."""

    def __init__(self) -> None:
        super().__init__(speaking=True)

    async def next_event(self):  # noqa: ANN202 - test protocol
        raise RuntimeError("recognizer stream died")


_LOUD_FRAME = (1000).to_bytes(2, "little", signed=True) * 480


@pytest.mark.asyncio
async def test_every_ungrounded_response_is_bounded(monkeypatch) -> None:
    """F5a: bounding only the opener left responses #2..n unbounded whenever
    the recognizer was absent or dead — the regime where the two-AIs
    self-talk loop ran unopposed and past grounded-path fixes 'did not
    stick'. Every failopen response now keeps the same coarse bound."""
    monkeypatch.setattr(codex_subscription_mod, "_OPENING_RESPONSE_MAX_S", 0.05)
    client = _Client()
    client.subscription = _keeps_stream_open()
    endpoint = _FakeAudioEndpoint(
        output_schedule=(
            # Response 1: second chunk ages past the bound.
            (0.0, _LOUD_FRAME),
            (0.1, _LOUD_FRAME),
            # Response 2: same shape — previously unbounded.
            (0.1, _LOUD_FRAME),
            (0.1, _LOUD_FRAME),
        )
    )
    session = await _provider(client, endpoint=endpoint).open_session(RealtimeSessionConfig())

    events = await _collect_until(session, stop_after=2, kind="turn_complete", timeout_s=3.0)

    assert [event.type for event in events].count("turn_complete") == 2
    diag = session.diagnostics()
    assert diag.get("opening_responses_bounded", 0) == 1
    assert diag.get("ungrounded_responses_bounded", 0) == 1, (
        "the second ungrounded response must be bounded exactly like the opener"
    )
    await session.close()


@pytest.mark.asyncio
async def test_recognizer_rebuild_restores_grounding(monkeypatch) -> None:
    """F5b: a dead recognizer stream is no longer a one-way latch. The
    rebuild reuses the ordinary build path (process-wide STT cache), so the
    call regains its grounding source instead of running ungrounded until
    hangup."""
    monkeypatch.setattr(codex_subscription_mod, "_RECOGNIZER_REBUILD_BACKOFF_S", 0.0)
    replacements = [
        _DyingTranscriber(),
        _ScriptedInputTranscriber(
            [
                InputTranscriptEvent(kind="speech_started"),
                InputTranscriptEvent(kind="transcript", text="Hello again.", is_final=True),
            ]
        ),
    ]
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        input_transcriber_factory=lambda: replacements.pop(0),
    ).open_session(RealtimeSessionConfig())

    events = await _collect_until(session, stop_after=1, kind="input_transcript", timeout_s=3.0)

    finals = [event for event in events if event.type == "input_transcript"]
    assert [event.text for event in finals] == ["Hello again."], (
        "the rebuilt recognizer must ground turns again"
    )
    assert not [event for event in events if event.type == "error"], (
        "a recovered stream death deserves no surface notice"
    )
    diag = session.diagnostics()
    assert diag.get("recognizer_rebuild_attempts", 0) == 1
    assert diag.get("recognizer_rebuilds_restored", 0) == 1
    await session.close()


@pytest.mark.asyncio
async def test_exhausted_rebuilds_degrade_out_loud_exactly_once(
    monkeypatch,
) -> None:
    """F5a+F5b: only after both rebuilds are spent does the call enter the
    bounded ungrounded regime — and it says so on the surface exactly once
    (AP-30), instead of the silent one-way latch that hid the degradation."""
    monkeypatch.setattr(codex_subscription_mod, "_RECOGNIZER_REBUILD_BACKOFF_S", 0.0)
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        input_transcriber_factory=_DyingTranscriber,
    ).open_session(RealtimeSessionConfig())

    events = await _collect_until(session, stop_after=1, kind="error", timeout_s=3.0)

    notices = [event for event in events if event.type == "error"]
    assert len(notices) == 1
    assert "stopped during this" in str(notices[0].error)
    assert notices[0].recoverable is True
    diag = session.diagnostics()
    assert diag.get("recognizer_rebuild_attempts", 0) == 2
    assert diag.get("recognizer_rebuilds_restored", 0) == 2
    assert diag.get("grounding_unavailable_notices", 0) == 1
    assert session._local_grounding_ok is False
    await session.close()


@pytest.mark.asyncio
async def test_failed_rebuilds_also_end_in_one_notice(monkeypatch) -> None:
    """The rebuild that cannot even BUILD (factory returns None) retries and
    then reports the same single degradation notice as a dying stream."""
    monkeypatch.setattr(codex_subscription_mod, "_RECOGNIZER_REBUILD_BACKOFF_S", 0.0)
    replacements: list = [_DyingTranscriber()]
    client = _Client()
    client.subscription = _keeps_stream_open()
    session = await _provider(
        client,
        input_transcriber_factory=lambda: replacements.pop(0) if replacements else None,
    ).open_session(RealtimeSessionConfig())

    events = await _collect_until(session, stop_after=1, kind="error", timeout_s=3.0)

    notices = [event for event in events if event.type == "error"]
    assert len(notices) == 1
    assert "could not be rebuilt" in str(notices[0].error)
    diag = session.diagnostics()
    assert diag.get("recognizer_rebuild_attempts", 0) == 2
    assert diag.get("recognizer_rebuilds_restored", 0) == 0
    assert diag.get("grounding_unavailable_notices", 0) == 1
    await session.close()


@pytest.mark.asyncio
async def test_a_failed_recognizer_build_is_announced_at_session_start() -> None:
    """F5a: a call whose recognizer BUILD failed used to degrade with only a
    build-time log line — the silently disarmed grounding gate. The surface
    now hears about it before the first frame, exactly once."""
    session = codex_subscription_mod._CodexSubscriptionRealtimeSession(
        client=_Client(),
        subscription=_Subscription(),
        thread_id="thread-1",
        answer_sdp="v=0\r\nanswer",
        audio_endpoint=_FakeAudioEndpoint(),
        input_transcriber=None,
        grounding_unavailable_reason="RuntimeError: no STT backend",
    )

    events = []
    async for event in session.receive():
        events.append(event)
        if len(events) >= 2:
            break

    assert events[0].type == "error"
    assert events[0].recoverable is True
    assert "RuntimeError: no STT backend" in str(events[0].error)
    assert "bounded" in str(events[0].error)
    assert session.diagnostics().get("grounding_unavailable_notices", 0) == 1
    await session.close()


@pytest.mark.asyncio
async def test_an_injected_none_recognizer_stays_silent() -> None:
    """A caller that explicitly builds the session without a recognizer (every
    test in this file, and any embedder that owns its own STT story) is a
    choice, not a degradation — no start-of-call notice."""
    client = _Client()
    session = await _provider(client).open_session(RealtimeSessionConfig())

    events = [event async for event in session.receive()]

    assert not [
        event
        for event in events
        if event.type == "error" and "speech recognition is unavailable" in str(event.error)
    ]
    await session.close()


def test_a_recognizer_build_failure_carries_its_reason(monkeypatch) -> None:
    """The reason travels to the session so the surface notice can name it."""
    real_import = codex_subscription_mod.importlib.import_module

    def _refusing_import(name, *args, **kwargs):
        if name == "jarvis.realtime.input_transcription":
            raise RuntimeError("no usable STT backend")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(codex_subscription_mod.importlib, "import_module", _refusing_import)
    provider = CodexSubscriptionRealtimeProvider()

    transcriber, reason = provider._build_input_transcriber_outcome(
        SimpleNamespace(input_language="de")
    )

    assert transcriber is None
    assert reason == "RuntimeError: no usable STT backend"


def test_session_language_reaches_the_recognizer(monkeypatch) -> None:
    """F5c happy path: the resolved session input language is threaded into
    the local recognizer, which pins it on the STT config it builds."""
    built = []

    class _NewTranscriber:
        def __init__(self, *, sample_rate: int, language=None) -> None:
            built.append((sample_rate, language))

    fake_module = SimpleNamespace(LocalInputTranscriber=_NewTranscriber)
    real_import = codex_subscription_mod.importlib.import_module
    monkeypatch.setattr(
        codex_subscription_mod.importlib,
        "import_module",
        lambda name, *a, **k: (
            fake_module
            if name == "jarvis.realtime.input_transcription"
            else real_import(name, *a, **k)
        ),
    )
    provider = CodexSubscriptionRealtimeProvider()

    provider._build_input_transcriber(SimpleNamespace(input_language="de"))
    provider._build_input_transcriber(SimpleNamespace(input_language="auto"))

    assert built == [
        (codex_subscription_mod._INPUT_RATE, "de"),
        (
            codex_subscription_mod._INPUT_RATE,
            None,
        ),
    ], "an explicit language is passed; 'auto' keeps the configured one"


def test_language_kwarg_rejection_warns_exactly_once(monkeypatch, caplog) -> None:
    """F5c: the tolerance probe for an out-of-tree recognizer without the
    ``language`` kwarg stays, but its failure is a one-shot WARNING — the
    silently swallowed capability (a DEBUG line) is how the un-pinned session
    language survived unreported (AP-30)."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(codex_subscription_mod, "_language_kwarg_rejected_warned", False)

    class _OldTranscriber:
        def __init__(self, *, sample_rate: int) -> None:
            self.sample_rate = sample_rate

    fake_module = SimpleNamespace(LocalInputTranscriber=_OldTranscriber)
    real_import = codex_subscription_mod.importlib.import_module
    monkeypatch.setattr(
        codex_subscription_mod.importlib,
        "import_module",
        lambda name, *a, **k: (
            fake_module
            if name == "jarvis.realtime.input_transcription"
            else real_import(name, *a, **k)
        ),
    )
    provider = CodexSubscriptionRealtimeProvider()
    cfg = SimpleNamespace(input_language="de")

    first = provider._build_input_transcriber(cfg)
    second = provider._build_input_transcriber(cfg)

    assert isinstance(first, _OldTranscriber)
    assert isinstance(second, _OldTranscriber), (
        "the probe must fall back, never lose the grounding source"
    )
    rejections = [
        record
        for record in caplog.records
        if "does not accept a session language" in record.getMessage()
    ]
    assert [record.levelno for record in rejections] == [
        logging.WARNING,
        logging.DEBUG,
    ]


class _RosterClient(_Client):
    """Client whose server publishes a live voice roster (codex-cli 0.147)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.roster_requests: list[str] = []

    async def realtime_list_voices(self, thread_id: str) -> dict:
        self.roster_requests.append(thread_id)
        return {
            "v1": ["Nova"],
            "v2": ["cove"],
            "defaultV1": "nova",
            "defaultV2": "cove",
        }


class _RosterRpcError(Exception):
    """Duck-typed stand-in for CodexAppServerRPCError."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class _RosterRejectingClient(_Client):
    async def realtime_list_voices(self, thread_id: str) -> dict:
        del thread_id
        raise _RosterRpcError("Method not found", -32601)


class _RosterDeadTransportClient(_Client):
    async def realtime_list_voices(self, thread_id: str) -> dict:
        del thread_id
        raise RuntimeError("app-server transport is gone")


@pytest.mark.asyncio
async def test_the_live_voice_roster_extends_the_audited_static_set() -> None:
    """The server's own listVoices answer is authoritative when readable, so
    a voice the nine-name frozenset never heard of still validates."""
    client = _RosterClient()
    session = await _provider(client).open_session(RealtimeSessionConfig(voice="nova"))

    assert client.roster_requests == ["thread-1"]
    _thread_id, start = client.realtime_starts[0]
    assert start["voice"] == "nova"
    await session.close()


@pytest.mark.asyncio
async def test_a_rejected_list_voices_falls_back_to_the_static_roster() -> None:
    """An older server build without the method (JSON-RPC refusal, has a
    ``code``) degrades to the audited nine-voice frozenset — capability
    probed, never version-string-gated in this adapter (AP-21/AP-22)."""
    session = await _provider(_RosterRejectingClient()).open_session(
        RealtimeSessionConfig(voice="cove")
    )
    await session.close()

    with pytest.raises(RuntimeError, match="unsupported voice"):
        await _provider(_RosterRejectingClient()).open_session(RealtimeSessionConfig(voice="nova"))


@pytest.mark.asyncio
async def test_a_transport_failure_during_list_voices_is_not_fatal(monkeypatch, caplog) -> None:
    """The roster refresh is ADVISORY: it only ever widens a roster that
    already works, so a timeout or a dead transport must fall back to the
    audited static set instead of aborting an open whose thread is already
    running. A voice the SERVER refuses still fails loudly at realtime_start."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(codex_subscription_mod, "_voice_roster_probe_warned", False)

    session = await _provider(_RosterDeadTransportClient()).open_session(
        RealtimeSessionConfig(voice="cove")
    )
    await session.close()

    second = await _provider(_RosterDeadTransportClient()).open_session(
        RealtimeSessionConfig(voice="cove")
    )
    await second.close()

    failures = [
        record
        for record in caplog.records
        if "could not answer thread/realtime/listVoices" in record.getMessage()
    ]
    assert [record.levelno for record in failures] == [
        logging.WARNING,
        logging.DEBUG,
    ], "one-shot warning, then quiet — advisory, but never swallowed (AP-30)"


@pytest.mark.asyncio
async def test_a_transport_failure_during_list_voices_keeps_the_static_bound() -> None:
    """Falling back is not the same as accepting anything: a voice outside
    the audited static roster is still refused when the live one is unreadable."""
    with pytest.raises(RuntimeError, match="unsupported voice"):
        await _provider(_RosterDeadTransportClient()).open_session(
            RealtimeSessionConfig(voice="nova")
        )


class _InstructionSlotClient(_Client):
    """Client that declares AND transmits the 0.147 realtimeStartInstructions."""

    def realtime_start_instructions_supported(self) -> bool:
        return True

    async def realtime_start(
        self,
        thread_id: str,
        *,
        realtime_start_instructions: str | None = None,
        **kwargs,
    ):
        kwargs["realtime_start_instructions"] = realtime_start_instructions
        self.realtime_starts.append((thread_id, kwargs))
        return SimpleNamespace(
            response=self.start_response,
            answer_sdp="v=0\r\nanswer",
        )


class _WithheldInstructionSlotClient(_InstructionSlotClient):
    """Declares the keyword but is bound to a binary that never sends it."""

    def realtime_start_instructions_supported(self) -> bool:
        return False


class _UnprobedInstructionSlotClient(_InstructionSlotClient):
    """Declares the keyword and offers no capability probe at all."""

    realtime_start_instructions_supported = None


@pytest.mark.asyncio
async def test_startup_contract_rides_the_instruction_slot_when_transmitted() -> None:
    """When the client declares realtime_start_instructions AND confirms it
    transmits the field, the startup contract travels as SESSION instructions
    instead of a trusted first prompt — injected prompt context is what the
    model audibly acknowledged mid-open."""
    client = _InstructionSlotClient()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(instructions="You are Nova.")
    )

    _thread_id, start = client.realtime_starts[0]
    assert start["trusted_prompt"] == ""
    assert "You are Nova." in start["realtime_start_instructions"]
    assert "Speak only the assistant side" in start["realtime_start_instructions"]
    await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_factory",
    [_WithheldInstructionSlotClient, _UnprobedInstructionSlotClient],
    ids=["probe-says-withheld", "no-probe-at-all"],
)
async def test_startup_contract_stays_on_the_prompt_when_not_transmitted(
    client_factory,
) -> None:
    """Declaration is not transmission. A client bound to a binary that drops
    the field — and one that cannot even answer the question — must keep the
    trusted prompt: blanking it there left the whole call with no persona,
    because the pre-marked context baseline suppresses every later re-send."""
    client = client_factory()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(instructions="You are Nova.")
    )

    _thread_id, start = client.realtime_starts[0]
    assert start["realtime_start_instructions"] is None
    assert "You are Nova." in start["trusted_prompt"]
    await session.close()


@pytest.mark.asyncio
async def test_startup_contract_stays_on_the_prompt_without_the_slot() -> None:
    """A client that does not DECLARE the parameter by name keeps the proven
    trusted-prompt path — a ``**kwargs`` sink would swallow the contract
    without sending it, the exact silent capability loss AP-30 forbids."""
    client = _Client()
    session = await _provider(client).open_session(
        RealtimeSessionConfig(instructions="You are Nova.")
    )

    _thread_id, start = client.realtime_starts[0]
    assert "realtime_start_instructions" not in start
    assert "You are Nova." in start["trusted_prompt"]
    await session.close()


@pytest.mark.asyncio
async def test_multibyte_startup_history_stays_under_the_server_byte_limit() -> None:
    """The app server bounds ``initialItems`` by UTF-8 BYTES, so the adapter
    must trim by bytes too. Trimming by characters let CJK history pass here
    at up to four bytes per character and then fail realtime_start with
    "startup history is too large" — precisely on the mid-call rebuild whose
    job is to restore a call quietly."""
    history = tuple(
        {"role": "user" if index % 2 == 0 else "assistant", "text": "\u5b9f\u884c" * 1_000}
        for index in range(codex_subscription_mod._HISTORY_MAX_ITEMS)
    )
    client = _Client()
    session = await _provider(client).open_session(RealtimeSessionConfig(history=history))

    _thread_id, start = client.realtime_starts[0]
    items = start["initial_items"]
    assert items, "the newest turns must still be seeded, not dropped wholesale"
    total_bytes = sum(len(item["text"].encode("utf-8")) for item in items)
    assert total_bytes <= _MAX_REALTIME_INITIAL_TEXT_BYTES, (
        "the emitted history must fit the limit the server itself validates"
    )
    assert total_bytes <= codex_subscription_mod._HISTORY_MAX_BYTES
    assert all(item["text"] == history[0]["text"][:2_000] for item in items)
    await session.close()


def test_ascii_startup_history_keeps_its_proven_bound() -> None:
    """The byte bound is the same number the character bound used to be, so
    plain-ASCII history is trimmed exactly as before — no silent regression
    in how much context a normal call carries."""
    history = tuple(
        {"role": "user", "text": "a" * 2_000}
        for _ in range(codex_subscription_mod._HISTORY_MAX_ITEMS)
    )

    items = codex_subscription_mod._history_initial_items(history)

    assert len(items) == codex_subscription_mod._HISTORY_MAX_BYTES // 2_000
