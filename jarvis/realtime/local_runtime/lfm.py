"""Streaming native audio adapter for the pinned LFM2.5 GGUF runner.

This runner accepts audio directly and returns interleaved text and PCM. Its
wire protocol does not implement tool calls/results or full-duplex input; those
capabilities must not be inferred from OpenAI-shaped response envelopes.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import math
import struct
from collections.abc import AsyncIterator
from typing import Any, Literal
from urllib.parse import urlsplit

from jarvis.core.http_pool import HttpClientPool

from .events import NativeAudioError as NativeAudioError
from .events import NativeAudioEvent as NativeAudioEvent

_MAX_EVENT_BYTES = 4 * 1024 * 1024
_AUDIO_RATES = frozenset({16_000, 24_000, 44_100, 48_000})


def loopback_root(value: str) -> str:
    """Private native engines never receive audio through an arbitrary URL."""
    parsed = urlsplit(value)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("A numeric loopback address is required for the native engine.") from exc
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("The native engine must use a private loopback HTTP endpoint.")
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{host}:{port}"


async def _sse_data(response: Any) -> AsyncIterator[str]:
    """Incrementally bound events before decoding JSON or allocating PCM."""
    buffer = bytearray()
    lines: list[bytes] = []
    event_size = 0
    # Do not set chunk_size: httpx would buffer small audio events until that
    # many bytes arrive, adding audible latency to a correctly streaming model.
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        while (end := buffer.find(b"\n")) >= 0:
            line = bytes(buffer[:end]).rstrip(b"\r")
            del buffer[: end + 1]
            if not line:
                if lines:
                    try:
                        yield b"\n".join(lines).decode("utf-8", errors="strict")
                    except UnicodeDecodeError as exc:
                        raise NativeAudioError(
                            "The native engine returned invalid text encoding."
                        ) from exc
                lines = []
                event_size = 0
            elif line.startswith(b"data:"):
                value = line[5:].lstrip(b" ")
                event_size += len(value)
                if event_size > _MAX_EVENT_BYTES:
                    raise NativeAudioError("The native engine returned an oversized event.")
                lines.append(value)
        if len(buffer) + event_size > _MAX_EVENT_BYTES:
            raise NativeAudioError("The native engine returned an oversized event.")
    if buffer or lines:
        raise NativeAudioError("The native engine ended inside a response event.")


class LfmAudioClient:
    """One conversation owns the resident runner's context at a time.

    On failure/cancellation, the next request MUST reset context: replaying an
    uncertain native generation would duplicate audio or retain a partial turn.
    A completed stream is protocol proof, not semantic/tool qualification.
    """

    supports_direct_tools = False
    supports_tool_results = False
    supports_full_duplex = False

    def __init__(
        self,
        base_url: str,
        *,
        transport: Any | None = None,
        wire_format: Literal["pcm16", "legacy_float32"] = "pcm16",
    ) -> None:
        self.base_url = loopback_root(base_url)
        if wire_format not in {"pcm16", "legacy_float32"}:
            raise ValueError("Unknown native audio wire format.")
        self._pool = HttpClientPool(timeout_s=60.0, transport=transport)
        self._busy = False
        self._has_context = False
        self._closed = False
        self._wire_format = wire_format

    async def generate(
        self,
        *,
        text: str = "",
        audio_wav: bytes | None = None,
        instructions: str = "Respond with interleaved text and audio.",
        continue_context: bool = False,
        output_mode: Literal["text", "audio", "text_audio"] = "text_audio",
        max_tokens: int = 32_768,
    ) -> AsyncIterator[NativeAudioEvent]:
        if self._closed:
            raise NativeAudioError("The native audio connection is closed.")
        if self._busy:
            raise NativeAudioError("The native audio model is already handling a turn.")
        if continue_context and not self._has_context:
            raise NativeAudioError("The native conversation must be reset before continuing.")
        if not text.strip() and audio_wav is None:
            raise ValueError("A native audio turn needs text or audio input.")
        if output_mode not in {"text", "audio", "text_audio"}:
            raise ValueError("Unknown native audio output mode.")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        self._busy = True
        self._has_context = False
        completed = False
        try:
            import httpx

            messages: list[dict[str, Any]] = []
            if not continue_context:
                messages.append({"role": "system", "content": instructions})
            content: list[dict[str, Any]] = []
            if text:
                content.append({"type": "text", "text": text})
            if audio_wav is not None:
                content.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64.b64encode(audio_wav).decode("ascii"),
                            "format": "wav",
                        },
                    }
                )
            messages.append({"role": "user", "content": content})
            payload = {
                "messages": messages,
                "stream": True,
                "reset_context": not continue_context,
                "max_tokens": max_tokens,
                "modalities": ["text", "audio"] if output_mode == "text_audio" else [output_mode],
            }
            saw_finish = False
            async with self._pool.client().stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise NativeAudioError(
                        f"Native audio request failed (HTTP {response.status_code})."
                    )
                if not response.headers.get("content-type", "").startswith("text/event-stream"):
                    raise NativeAudioError(
                        "The native engine did not return an audio event stream."
                    )
                async for data in _sse_data(response):
                    if data == "[DONE]":
                        if not saw_finish:
                            raise NativeAudioError(
                                "The native engine omitted its completion event."
                            )
                        completed = True
                        self._has_context = True
                        yield NativeAudioEvent(kind="done")
                        break
                    try:
                        event = json.loads(data)
                    except ValueError as exc:
                        raise NativeAudioError(
                            "The native engine returned invalid event data."
                        ) from exc
                    if not isinstance(event, dict) or "error" in event:
                        raise NativeAudioError("The native engine reported an inference failure.")
                    choices = event.get("choices")
                    if not isinstance(choices, list) or len(choices) != 1:
                        raise NativeAudioError("The native engine returned an invalid choice.")
                    choice = choices[0]
                    if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                        raise NativeAudioError(
                            "The native engine returned an invalid output delta."
                        )
                    if saw_finish:
                        raise NativeAudioError("The native engine emitted output after completion.")
                    delta = choice["delta"]
                    if delta.get("tool_calls") or delta.get("function_call"):
                        raise NativeAudioError(
                            "This native adapter has no verified tool-call channel."
                        )
                    value = delta.get("content")
                    if value is not None:
                        if not isinstance(value, str):
                            raise NativeAudioError(
                                "The native engine returned a non-text transcript."
                            )
                        if value and output_mode != "audio":
                            yield NativeAudioEvent(kind="text", text=value)
                    audio_field = (
                        "audio_chunk" if self._wire_format == "legacy_float32" else "audio"
                    )
                    if any(key.startswith("audio") and key != audio_field for key in delta):
                        raise NativeAudioError("The native engine's audio wire format changed.")
                    if audio_field in delta:
                        audio = delta[audio_field]
                        if not isinstance(audio, dict):
                            raise NativeAudioError(
                                "The native engine returned invalid audio metadata."
                            )
                        rate = audio.get("sample_rate")
                        if (
                            audio.get("format") != "pcm"
                            or type(rate) is not int
                            or rate not in _AUDIO_RATES
                        ):
                            raise NativeAudioError(
                                "The native engine returned an unsupported audio format."
                            )
                        try:
                            pcm = base64.b64decode(audio.get("data", ""), validate=True)
                        except (ValueError, TypeError, binascii.Error) as exc:
                            raise NativeAudioError(
                                "The native engine returned invalid audio bytes."
                            ) from exc
                        if not pcm or len(pcm) % 2:
                            raise NativeAudioError(
                                "The native engine returned incomplete PCM samples."
                            )
                        if self._wire_format == "legacy_float32":
                            if len(pcm) % 4:
                                raise NativeAudioError(
                                    "The native engine returned incomplete float samples."
                                )
                            converted = bytearray()
                            for (sample,) in struct.iter_unpack("<f", pcm):
                                if not math.isfinite(sample):
                                    raise NativeAudioError(
                                        "The native engine returned non-finite audio."
                                    )
                                converted.extend(
                                    struct.pack("<h", round(max(-1.0, min(1.0, sample)) * 32767))
                                )
                            pcm = bytes(converted)
                        # The pinned legacy binary may generate audio even for a
                        # text-only request. Never play an internal JSON/tool
                        # proposal merely because that runner ignored modalities.
                        if output_mode != "text":
                            yield NativeAudioEvent(kind="audio", pcm=pcm, sample_rate=rate)
                    finish = choice.get("finish_reason")
                    if finish is not None:
                        if finish != "stop":
                            raise NativeAudioError(
                                "The native engine did not finish the response normally."
                            )
                        saw_finish = True
                if not completed:
                    raise NativeAudioError(
                        "The native engine disconnected before completing the turn."
                    )
        except httpx.HTTPError as exc:
            raise NativeAudioError("The native audio connection failed.") from exc
        finally:
            self._busy = False
            if not completed:
                self._has_context = False

    async def close(self) -> None:
        self._closed = True
        self._has_context = False
        await self._pool.aclose()
