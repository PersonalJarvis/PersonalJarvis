"""Wire protocol v1: framing round trips and malformed input."""

from __future__ import annotations

import struct

import pytest

from jarvis.voice_engine import protocol as p


def test_json_and_audio_frames_round_trip_across_arbitrary_splits() -> None:
    message = {"type": p.TRANSCRIPT_INPUT, "text": "Grüße", "final": True}  # i18n-allow
    pcm = bytes(range(256)) * 4
    stream = p.encode_json(message) + p.encode_audio(3, 7, pcm) + p.encode_json({"type": "x"})
    reader = p.FrameReader()
    frames = []
    for i in range(0, len(stream), 5):
        frames.extend(reader.feed(stream[i : i + 5]))
    assert frames[0] == message
    assert frames[1] == p.AudioFrame(slot=3, seq=7, pcm=pcm)
    assert frames[2] == {"type": "x"}
    assert reader.pending_bytes == 0


def test_partial_frames_wait_for_the_rest() -> None:
    data = p.encode_json({"type": "hello"})
    reader = p.FrameReader()
    assert reader.feed(data[:-1]) == []
    assert reader.feed(data[-1:]) == [{"type": "hello"}]


@pytest.mark.parametrize(
    "payload",
    [b"Jnot json", b"J[1, 2]", b'J{"no_type": 1}', b"Xabc", b"A\x00"],
)
def test_malformed_payloads_are_rejected(payload: bytes) -> None:
    with pytest.raises(p.ProtocolError):
        p.FrameReader().feed(struct.pack(">I", len(payload)) + payload)


def test_oversized_length_is_rejected_before_buffering() -> None:
    with pytest.raises(p.ProtocolError):
        p.FrameReader().feed(struct.pack(">I", p.MAX_FRAME_BYTES + 1) + b"J")
    with pytest.raises(p.ProtocolError):
        p.encode_audio(0, 0, b"\x00" * (p.MAX_FRAME_BYTES + 1))
