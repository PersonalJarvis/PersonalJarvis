"""Native runner wire contracts using controlled byte streams, not inference mocks."""

from __future__ import annotations

import asyncio
import base64
import json
import struct

import httpx
import pytest

from jarvis.realtime.local_runtime.lfm import LfmAudioClient, NativeAudioError, loopback_root


def event(delta=None, finish=None) -> bytes:
    return (
        "data: "
        + json.dumps({"choices": [{"delta": delta or {}, "finish_reason": finish}]})
        + "\n\n"
    ).encode()


def completed() -> bytes:
    return event(finish="stop") + b"data: [DONE]\n\n"


def client_for(payload: bytes) -> LfmAudioClient:
    return LfmAudioClient(
        "http://127.0.0.1:9999",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=payload, headers={"content-type": "text/event-stream"}
            ),
        ),
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.example:443",
        "http://192.168.1.2:8080",
        "http://localhost:8080",
        "http://127.0.0.1:8080/v1",
        "http://user:pass@127.0.0.1:8080",
        "http://127.0.0.1:8080?key=x",
    ],
)
def test_native_audio_never_uses_an_arbitrary_remote_endpoint(url: str) -> None:
    with pytest.raises(ValueError):
        loopback_root(url)


def test_ipv6_loopback_is_supported() -> None:
    assert loopback_root("http://[::1]:1234/") == "http://[::1]:1234"


@pytest.mark.asyncio
async def test_audio_and_text_are_streamed_in_order_and_not_called_tools() -> None:
    pcm = b"\x00\x01" * 8
    client = client_for(
        event({"content": "Hello"})
        + event(
            {
                "audio": {
                    "format": "pcm",
                    "sample_rate": 24_000,
                    "data": base64.b64encode(pcm).decode(),
                }
            }
        )
        + completed()
    )
    try:
        events = [item async for item in client.generate(text="Hello")]
        assert [item.kind for item in events] == ["text", "audio", "done"]
        assert events[1].pcm == pcm
        assert events[1].sample_rate == 24_000
        assert not client.supports_direct_tools
        assert not client.supports_tool_results
        assert not client.supports_full_duplex
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_context_continues_only_after_completed_turn() -> None:
    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, content=completed(), headers={"content-type": "text/event-stream"}
        )

    client = LfmAudioClient("http://127.0.0.1:1234", transport=httpx.MockTransport(reply))
    try:
        with pytest.raises(NativeAudioError, match="reset"):
            _ = [item async for item in client.generate(text="next", continue_context=True)]
        _ = [item async for item in client.generate(text="first")]
        _ = [item async for item in client.generate(text="next", continue_context=True)]
        assert requests[0]["reset_context"] is True
        assert requests[1]["reset_context"] is False
        assert [message["role"] for message in requests[1]["messages"]] == ["user"]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "payload",
    [
        event({"content": "unfinished"}),
        b"data: [DONE]\n\n",
        b"data: broken\n\n",
        b'data: {"error": {"message": "private upstream body"}}\n\n',
        event({"audio": {"format": "pcm", "sample_rate": 24_000, "data": "AAAA"}}) + completed(),
        event({"audio": {"format": "mp3", "sample_rate": 24_000, "data": "AAAA"}}) + completed(),
        event({"tool_calls": [{"name": "fake_action"}]}) + completed(),
        event(finish="length") + b"data: [DONE]\n\n",
        event(finish="stop") + event({"content": "late"}) + b"data: [DONE]\n\n",
    ],
)
@pytest.mark.asyncio
async def test_malformed_or_incomplete_stream_never_reports_success(payload: bytes) -> None:
    client = client_for(payload)
    try:
        with pytest.raises(NativeAudioError) as error:
            _ = [item async for item in client.generate(text="first")]
        assert "private upstream body" not in str(error.value)
        with pytest.raises(NativeAudioError, match="reset"):
            _ = [item async for item in client.generate(text="next", continue_context=True)]
    finally:
        await client.close()


class PausedStream(httpx.AsyncByteStream):
    def __init__(self):
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        yield event({"content": "first"})
        await self.release.wait()
        yield completed()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_first_event_does_not_wait_for_a_large_buffer_and_cancel_closes_stream() -> None:
    stream = PausedStream()
    client = LfmAudioClient(
        "http://127.0.0.1:1234",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=stream, headers={"content-type": "text/event-stream"}
            ),
        ),
    )
    turn = client.generate(text="hello")
    try:
        first = await asyncio.wait_for(anext(turn), timeout=1)
        assert first.text == "first"
        with pytest.raises(NativeAudioError, match="already handling"):
            _ = [item async for item in client.generate(text="competing")]
        await turn.aclose()
        assert stream.closed
        with pytest.raises(NativeAudioError, match="reset"):
            _ = [item async for item in client.generate(text="next", continue_context=True)]
    finally:
        await turn.aclose()
        await client.close()


@pytest.mark.asyncio
async def test_pinned_legacy_float_audio_is_converted_with_explicit_wire_selection() -> None:
    floats = struct.pack("<fff", -0.5, 0.0, 0.5)
    payload = (
        event(
            {
                "audio_chunk": {
                    "data": base64.b64encode(floats).decode(),
                    "format": "pcm",
                    "sample_rate": 24_000,
                }
            }
        )
        + completed()
    )
    client = LfmAudioClient(
        "http://127.0.0.1:1234",
        wire_format="legacy_float32",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=payload, headers={"content-type": "text/event-stream"}
            ),
        ),
    )
    try:
        events = [item async for item in client.generate(text="hello")]
        assert struct.unpack("<hhh", events[0].pcm) == (-16384, 0, 16384)
        assert events[0].sample_rate == 24_000
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_wire_format_change_is_not_silently_reported_as_success() -> None:
    client = client_for(event({"audio_chunk": {"data": "AAAA"}}) + completed())
    try:
        with pytest.raises(NativeAudioError, match="wire format"):
            _ = [item async for item in client.generate(text="hello")]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_text_only_requests_never_emit_spoken_internal_output() -> None:
    client = client_for(
        event({"content": '{"name":"get_weather"}'})
        + event(
            {
                "audio": {
                    "format": "pcm",
                    "sample_rate": 24_000,
                    "data": base64.b64encode(b"\x00\x01").decode(),
                }
            }
        )
        + completed()
    )
    try:
        events = [item async for item in client.generate(text="hello", output_mode="text")]
        assert [item.kind for item in events] == ["text", "done"]
    finally:
        await client.close()
