"""Subscription Live keeps OAuth billing, host control and single media ownership."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from jarvis.plugins.realtime.openai_subscription_live import (
    CALL_URL,
    OpenAISubscriptionLiveConnection,
    SubscriptionLiveError,
)
from tests.fakes.fake_subscription_live_wire import (
    SDP,
    FakeSubscriptionLiveWire,
    FakeSubscriptionSocket,
)


def connection(events=None):
    socket = FakeSubscriptionSocket(events)
    return OpenAISubscriptionLiveConnection(socket, session_id="rtc_fake", answer_sdp=SDP)


def delegation(identifier="delegation-1"):
    return {
        "type": "delegation.created",
        "item": {
            "type": "delegation",
            "target": "client",
            "id": identifier,
            "content": [{"type": "input_text", "text": "Read the current time."}],
        },
    }


async def test_call_uses_only_codex_oauth_and_host_owned_delegation():
    wire = FakeSubscriptionLiveWire()
    provider = wire.provider()
    result = await provider.open_session(
        wire.config(
            store=False,
            instructions="Keep tools owned by the host.",
            delegation={"type": "responses", "model": "must-not-forward"},
        )
    )
    request = wire.requests[0]
    assert str(request.url) == CALL_URL
    assert request.headers["authorization"] == "Bearer fake-original"
    assert request.headers["chatgpt-account-id"] == "fake-account"
    assert request.headers["openai-alpha"] == "quicksilver=v2"
    payload = json.loads(request.content)
    assert payload["session"]["delegation"] == {"type": "client"}
    assert "store" not in payload["session"]
    assert "must-not-forward" not in request.content.decode()
    assert wire.connects[0][0] == "wss://api.openai.com/v1/live/rtc_fake"
    assert result.answer_sdp == SDP
    assert result.session_id == "rtc_fake"
    assert result.requires_close_ack is False
    assert provider.credential_candidates == ()
    assert provider.implicit_usage_fallback_allowed is False
    assert not wire.socket.sent  # Existing-call attachment sends no new start/update.
    await result.close()


async def test_creation_refreshes_only_once_after_rejected_authentication():
    wire = FakeSubscriptionLiveWire(
        [
            httpx.Response(401, text="private account information"),
            httpx.Response(201, text=SDP, headers={"openai-session-id": "rtc_rotated"}),
        ]
    )
    result = await wire.provider().open_session(wire.config())
    assert wire.auth_refreshes == [False, True]
    assert wire.requests[1].headers["authorization"] == "Bearer fake-refreshed"
    assert wire.requests[0].headers["session-id"] == wire.requests[1].headers["session-id"]
    assert wire.connects[0][1]["additional_headers"]["Authorization"] == "Bearer fake-refreshed"
    await result.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_required"),
        (403, "access_denied"),
        (429, "rate_limited"),
        (500, "provider_error"),
        (302, "provider_error"),
    ],
)
async def test_rejected_creation_never_falls_back_or_exposes_body(status, code):
    wire = FakeSubscriptionLiveWire(
        [
            httpx.Response(
                status, text="provider-private-secret", headers={"location": "https://evil.invalid"}
            ),
            httpx.Response(status, text="provider-private-secret"),
        ]
    )
    with pytest.raises(SubscriptionLiveError) as caught:
        await wire.provider().open_session(wire.config())
    assert caught.value.code == code
    assert "provider-private-secret" not in str(caught.value)
    assert not wire.connects
    assert len(wire.requests) == (2 if status == 401 else 1)


@pytest.mark.parametrize(
    "answer,headers",
    [
        ("", {"location": "/v1/live/rtc_fake"}),
        ("not an SDP", {"location": "/v1/live/rtc_fake"}),
        (SDP, {"location": "/v1/live/../../secret"}),
        (SDP, {}),
        ("v=0\r\nm=audio " + "a" * (256 * 1024), {"location": "/v1/live/rtc_fake"}),
    ],
    ids=["empty-sdp", "invalid-sdp", "invalid-location", "missing-call-id", "oversized-sdp"],
)
async def test_invalid_provider_answers_are_bounded_and_cannot_choose_control_url(answer, headers):
    wire = FakeSubscriptionLiveWire([httpx.Response(201, text=answer, headers=headers)])
    with pytest.raises(SubscriptionLiveError, match="invalid response"):
        await wire.provider().open_session(wire.config())
    assert len(wire.requests) == 1
    if headers.get("location") == "/v1/live/rtc_fake":
        assert len(wire.connects) == 1
        assert wire.socket.sent == [{"type": "session.close"}]
        assert wire.socket.closed == 1
    else:
        assert not wire.connects


async def test_a_foreign_location_never_receives_oauth_credentials():
    wire = FakeSubscriptionLiveWire(
        [
            httpx.Response(
                201,
                text=SDP,
                headers={"location": "https://evil.invalid/private/rtc_existing"},
            )
        ]
    )
    result = await wire.provider().open_session(wire.config())
    assert wire.connects[0][0] == "wss://api.openai.com/v1/live/rtc_existing"
    await result.close()


@pytest.mark.parametrize(
    "field,value", [("model", "gpt-live-1"), ("audio", {"output": {"voice": "gleam"}})]
)
async def test_api_model_or_voice_is_rejected_before_auth_or_network(field, value):
    wire = FakeSubscriptionLiveWire()
    with pytest.raises(SubscriptionLiveError, match="configuration"):
        await wire.provider().open_session(wire.config(**{field: value}))
    assert wire.auth_refreshes == []
    assert wire.requests == []


@pytest.mark.parametrize(
    "media", ["m=video 9 UDP/TLS/RTP/SAVPF 96", "m=application 9 UDP/DTLS/SCTP webrtc-datachannel"]
)
async def test_subscription_offer_cannot_create_a_competing_browser_control_channel(media):
    wire = FakeSubscriptionLiveWire()
    config = wire.config()
    config.offer_sdp += media + "\r\n"
    with pytest.raises(ValueError, match="WebRTC audio offer"):
        await wire.provider().open_session(config)
    assert wire.requests == [] and wire.auth_refreshes == []


async def test_sideband_indexing_retry_reuses_one_allocation_and_pays_shared_budget(monkeypatch):
    import jarvis.plugins.realtime.openai_subscription_live as module

    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    error = RuntimeError("private wire details")
    error.response = SimpleNamespace(status_code=404)
    wire = FakeSubscriptionLiveWire()
    wire.connect_errors.append(error)
    result = await wire.provider().open_session(wire.config())
    assert len(wire.requests) == 1
    assert len(wire.connects) == 2
    assert wire.permits == 1
    assert len(delays) == 1 and 0.15 <= delays[0] <= 0.35
    await result.close()


async def test_unknown_sideband_failure_is_sanitized_and_never_creates_a_second_call():
    wire = FakeSubscriptionLiveWire()
    wire.connect_errors.append(OSError("private credential echo"))
    with pytest.raises(SubscriptionLiveError) as caught:
        await wire.provider().open_session(wire.config())
    assert caught.value.code == "control_unavailable"
    assert caught.value.allocation_unconfirmed is True
    assert "credential echo" not in str(caught.value)
    assert len(wire.requests) == 1
    assert len(wire.connects) == 2  # One attempt to retire that same allocation.
    assert wire.socket.sent == [{"type": "session.close"}]
    assert wire.socket.closed == 1


async def test_overlapping_speakers_and_final_corrections_keep_stable_segments():
    conn = connection(
        [
            {"type": "input_transcript.added", "event_id": "u1", "item": {"text": "Open the old"}},
            {"type": "output_transcript.added", "event_id": "a1", "item": {"text": "Checking"}},
            {"type": "input_transcript.added", "event_id": "u2", "item": {"text": " file."}},
            {
                "type": "turn.done",
                "event_id": "uf",
                "turn": {"role": "user", "transcript": "Open the new file."},
            },
            {"type": "input_transcript.added", "event_id": "u3", "item": {"text": "Thanks."}},
        ]
    )
    user_first, assistant, user_next, final, next_turn = [await conn.receive() for _ in range(5)]
    assert user_first["segment_id"] == user_next["segment_id"] == final["segment_id"]
    assert assistant["segment_id"] != final["segment_id"] != next_turn["segment_id"]
    assert user_next["transcript"] == "Open the old file."
    assert final["transcript"] == "Open the new file."
    assert final["is_final"] is True
    assert user_first["timestamp_source"] == "local_receive_clock"
    assert final["start_ms"] == user_first["start_ms"]


async def test_duplicate_provider_events_and_delegations_cannot_repeat_work():
    transcript = {"type": "input_transcript.added", "event_id": "once", "item": {"text": "Hello"}}
    conn = connection([transcript, transcript, delegation(), delegation()])
    assert (await conn.receive())["transcript"] == "Hello"
    assert (await conn.receive())["type"] == "subscription.ignored"
    handoff = await conn.receive()
    assert handoff["type"] == "session.delegation.created"
    assert handoff["prompt"] == "Read the current time."
    assert (await conn.receive())["type"] == "subscription.ignored"


async def test_context_results_are_utf8_bounded_and_keep_delegation_ownership():
    conn = connection([delegation()])
    await conn.receive()
    text = "Result " + "\U0001f600" * 300
    await conn.send(
        {"type": "session.commentary.append", "delegation_id": "delegation-1", "content": text}
    )
    frames = conn.socket.sent
    assert all(frame["type"] == "delegation.context.append" for frame in frames)
    assert all(frame["delegation_item_id"] == "delegation-1" for frame in frames)
    assert all(frame["channel"] == "speakable" for frame in frames)
    assert all(len(frame["content"][0]["text"].encode("utf-8")) <= 500 for frame in frames)
    assert "".join(frame["content"][0]["text"] for frame in frames) == text
    await conn.send(
        {"type": "session.thinking.append", "delegation_id": None, "content": "Background"}
    )
    assert conn.socket.sent[-1]["type"] == "session.context.append"
    assert conn.socket.sent[-1]["channel"] == "commentary"


async def test_unknown_delegation_and_api_commands_cannot_reach_provider():
    conn = connection()
    with pytest.raises(ValueError, match="Unknown"):
        await conn.send(
            {"type": "session.commentary.append", "delegation_id": "alien", "content": "result"}
        )
    for event in (
        {"type": "response.create"},
        {"type": "session.input_audio.append", "audio": "AAAA"},
    ):
        with pytest.raises(ValueError, match="Unsupported"):
            await conn.send(event)
    assert conn.socket.sent == []


async def test_audio_stays_on_webrtc_and_provider_error_is_fatal_but_safe():
    conn = connection(
        [
            {"type": "output_audio.delta", "audio": "AAAA"},
            {
                "type": "error",
                "error": {"code": "usage_limit_reached", "message": "private credential"},
            },
        ]
    )
    assert (await conn.receive())["type"] == "subscription.ignored"
    event = await conn.receive()
    assert event["fatal"] is True
    assert event["error"]["code"] == "quota_exhausted"
    assert "private credential" not in json.dumps(event)


@pytest.mark.parametrize(
    "event", ["{bad-json", [], {"type": "input_transcript.added", "item": None}]
)
async def test_bad_frames_fail_closed(event):
    conn = connection([event])
    with pytest.raises(SubscriptionLiveError, match="invalid response"):
        await conn.receive()


async def test_read_error_is_terminal_without_automatic_replay():
    conn = connection([OSError("private websocket reason")])
    with pytest.raises(SubscriptionLiveError, match="connection was lost"):
        await conn.receive()
    assert conn.socket.sent == []


async def test_close_is_idempotent_and_never_claims_provider_acknowledgment():
    conn = connection()
    await conn.send({"type": "session.close"})
    await conn.close()
    await conn.close()
    assert conn.socket.sent == [{"type": "session.close"}]
    assert conn.socket.closed == 1
    assert conn.provider_closed is False
    with pytest.raises(SubscriptionLiveError):
        await conn.send({"type": "session.commentary.append", "content": "late result"})


async def test_failed_close_frame_still_closes_owned_socket():
    conn = connection()
    conn.socket.send_error = OSError("private message")
    await conn.close()
    assert conn.socket.closed == 1
    assert conn.provider_closed is False


async def test_read_cancellation_is_not_reclassified_as_provider_failure():
    conn = connection()
    task = asyncio.create_task(conn.receive())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await conn.close()


async def test_reattach_preserves_live_call_transcripts_and_exactly_once_handoffs(monkeypatch):
    import jarvis.plugins.realtime.openai_subscription_live as module

    async def sleep(_delay):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    wire = FakeSubscriptionLiveWire()
    provider = wire.provider()
    previous = await provider.open_session(wire.config())
    previous.socket.events.extend(
        [
            {"type": "input_transcript.added", "event_id": "before", "item": {"text": "Read "}},
            delegation(),
        ]
    )
    first = await previous.receive()
    await previous.receive()
    old_socket = previous.socket
    wire.socket = FakeSubscriptionSocket(
        [
            {"type": "input_transcript.added", "event_id": "after", "item": {"text": "the time."}},
            delegation(),
            {"type": "output_transcript.added", "item": {"text": "The time is"}},
        ]
    )
    resumed = await provider.reattach_session(previous)
    assert resumed is not None and resumed is not previous
    await previous.close()
    assert old_socket.closed == 1 and old_socket.sent == []
    assert resumed.session_id == previous.session_id
    assert len(wire.requests) == 1 and wire.permits == 1
    assert wire.connects[0][0] == wire.connects[1][0]
    after = await resumed.receive()
    assert after["segment_id"] == first["segment_id"]
    assert after["transcript"] == "Read the time."
    assert (await resumed.receive())["type"] == "subscription.ignored"
    assert (await resumed.receive())["role"] == "assistant"
    await resumed.close()
    assert wire.socket.sent == [{"type": "session.close"}]


@pytest.mark.parametrize("status", [404, 410])
async def test_reattach_returns_none_only_when_provider_says_call_ended(status, monkeypatch):
    import jarvis.plugins.realtime.openai_subscription_live as module

    async def sleep(_delay):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    wire = FakeSubscriptionLiveWire()
    provider = wire.provider()
    previous = await provider.open_session(wire.config())
    error = RuntimeError("private details")
    error.response = SimpleNamespace(status_code=status)
    wire.connect_errors.append(error)
    assert await provider.reattach_session(previous) is None
    assert len(wire.requests) == 1 and len(wire.connects) == 2
    assert previous.provider_closed is False  # Absence is not a close acknowledgment.


async def test_reattach_auth_refresh_never_reallocates_the_voice_call(monkeypatch):
    import jarvis.plugins.realtime.openai_subscription_live as module

    async def sleep(_delay):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    wire = FakeSubscriptionLiveWire()
    provider = wire.provider()
    previous = await provider.open_session(wire.config())
    error = RuntimeError("private details")
    error.response = SimpleNamespace(status_code=401)
    wire.connect_errors.append(error)
    wire.socket = FakeSubscriptionSocket()
    resumed = await provider.reattach_session(previous)
    assert resumed is not None
    assert wire.auth_refreshes == [False, False, True]
    assert wire.permits == 2
    assert len(wire.requests) == 1
    assert wire.connects[-1][1]["additional_headers"]["Authorization"] == "Bearer fake-refreshed"
    await resumed.close()


async def test_cancelled_setup_retires_allocated_call_without_new_mutation():
    wire = FakeSubscriptionLiveWire()
    original_connect = wire.connect
    attempts = 0

    async def connector(url, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError
        return await original_connect(url, **kwargs)

    provider = wire.provider()
    provider._websocket_connect = connector
    with pytest.raises(asyncio.CancelledError):
        await provider.open_session(wire.config())
    assert len(wire.requests) == 1
    assert wire.socket.sent == [{"type": "session.close"}]
    assert wire.socket.closed == 1
