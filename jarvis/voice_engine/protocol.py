"""Wire protocol v1 between the app's provider adapter and the engine worker.

The worker is a child process; the adapter writes frames to its stdin and
reads frames from its stdout (``docs/local-live-voice-rebuild.md`` 4.5).
Every frame is::

    length: uint32 big-endian   (bytes after this field)
    kind:   1 byte              b"J" = UTF-8 JSON object, b"A" = audio
    payload

An audio payload starts with an 8-byte header — ``slot: uint16`` (the session
the audio belongs to, assigned by the adapter in ``session.open``) and
``seq: uint32`` plus two reserved bytes — followed by mono PCM16
little-endian samples: 16 kHz towards the worker, 24 kHz back.

The format does not depend on the transport; a socket can carry the same
frames later.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
KIND_JSON = b"J"
KIND_AUDIO = b"A"
MAX_FRAME_BYTES = 1 << 20  # 1 MiB: far above any audio chunk or tool payload
_LENGTH = struct.Struct(">I")
_AUDIO_HEADER = struct.Struct(">HIH")

INPUT_RATE = 16_000
OUTPUT_RATE = 24_000

# Adapter -> worker
HELLO = "hello"
CONFIGURE = "configure"
SESSION_OPEN = "session.open"
SESSION_UPDATE = "session.update"
SESSION_CLOSE = "session.close"
RESPONSE_REQUEST = "response.request"
TOOL_RESULT = "tool.result"
TEXT = "text"
INTERRUPT = "interrupt"
TRUNCATE = "truncate"
SELFTEST = "selftest"
SHUTDOWN = "shutdown"

# Worker -> adapter
STATE = "state"
SESSION_READY = "session.ready"
SPEECH_STARTED = "speech_started"
TRANSCRIPT_INPUT = "transcript.input"
TRANSCRIPT_OUTPUT = "transcript.output"
TOOL_CALL = "tool.call"
RESPONSE_DONE = "response.done"
INTERRUPTED = "interrupted"
USAGE = "usage"
METRICS = "metrics"
ERROR = "error"
SELFTEST_RESULT = "selftest.result"


class ProtocolError(ValueError):
    """A frame that cannot be valid: wrong kind, oversized or malformed."""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    slot: int
    seq: int
    pcm: bytes


Frame = dict[str, Any] | AudioFrame


def encode_json(message: dict[str, Any]) -> bytes:
    payload = KIND_JSON + json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(f"message of {len(payload)} bytes exceeds the frame limit")
    return _LENGTH.pack(len(payload)) + payload


def encode_audio(slot: int, seq: int, pcm: bytes) -> bytes:
    payload = KIND_AUDIO + _AUDIO_HEADER.pack(slot, seq & 0xFFFFFFFF, 0) + pcm
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(f"audio frame of {len(payload)} bytes exceeds the frame limit")
    return _LENGTH.pack(len(payload)) + payload


def decode_payload(payload: bytes) -> Frame:
    if not payload:
        raise ProtocolError("empty frame")
    kind, body = payload[:1], payload[1:]
    if kind == KIND_JSON:
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid JSON frame: {exc}") from exc
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ProtocolError("a JSON frame must be an object with a string 'type'")
        return message
    if kind == KIND_AUDIO:
        if len(body) < _AUDIO_HEADER.size:
            raise ProtocolError("audio frame shorter than its header")
        slot, seq, _reserved = _AUDIO_HEADER.unpack_from(body)
        return AudioFrame(slot=slot, seq=seq, pcm=bytes(body[_AUDIO_HEADER.size :]))
    raise ProtocolError(f"unknown frame kind {kind!r}")


class FrameReader:
    """Incremental decoder: feed raw bytes, receive whole frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while len(self._buffer) >= _LENGTH.size:
            (length,) = _LENGTH.unpack_from(self._buffer)
            if length > MAX_FRAME_BYTES or length == 0:
                raise ProtocolError(f"frame length {length} is outside 1..{MAX_FRAME_BYTES}")
            end = _LENGTH.size + length
            if len(self._buffer) < end:
                break
            payload = bytes(self._buffer[_LENGTH.size : end])
            del self._buffer[:end]
            frames.append(decode_payload(payload))
        return frames

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)
