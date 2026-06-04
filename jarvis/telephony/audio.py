"""Audio transcode + framing helpers for the Twilio Media Streams bridge.

Twilio Media Streams carry 8 kHz mono 8-bit **mu-law** PCM, base64-encoded,
in ~20 ms frames (160 mu-law bytes per frame). Jarvis's STT wants 16 kHz mono
int16 and its TTS emits 24 kHz mono int16 (Gemini Charon). This module owns
every codec/resample/framing conversion between those worlds, plus a tiny
energy + silence-timeout endpointer so the telephony path needs no PyTorch /
Silero download to run headless (AD-T5/AD-T6).

Conversions use the stdlib ``audioop`` module (Python 3.11 here). On Python
3.13+ ``audioop`` is removed from the stdlib; the ``[telephony]`` extra then
pulls in the ``audioop-lts`` backport (declared in ``pyproject.toml``). The
import below transparently falls back to it.

State discipline (AD-T5): ``audioop.ratecv`` is stateful — feeding it audio
in chunks without carrying its filter state forward produces clicks at every
chunk boundary. ``Resampler`` holds that state per stream + direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:  # stdlib on <=3.12, backport ("audioop-lts") on >=3.13
    import audioop  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.13+
    import audioop_lts as audioop  # type: ignore[import-not-found, no-redef]

# Twilio Media Streams wire format.
TWILIO_SAMPLE_RATE: int = 8_000
TWILIO_FRAME_MS: int = 20
# 8000 Hz * 0.020 s = 160 samples; mu-law is 1 byte/sample -> 160 bytes/frame.
TWILIO_FRAME_BYTES: int = TWILIO_SAMPLE_RATE * TWILIO_FRAME_MS // 1000  # 160

# Jarvis-internal rates.
STT_SAMPLE_RATE: int = 16_000  # faster-whisper requires exactly this (fwhisper.py:106)
TTS_SAMPLE_RATE: int = 24_000  # Gemini Charon output

# int16 is 2 bytes/sample.
_WIDTH = 2


# ---------------------------------------------------------------------------
# mu-law <-> linear PCM
# ---------------------------------------------------------------------------


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """Decode 8-bit mu-law bytes to 16-bit linear PCM (same sample rate)."""
    if not ulaw_bytes:
        return b""
    return audioop.ulaw2lin(ulaw_bytes, _WIDTH)


def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Encode 16-bit linear PCM to 8-bit mu-law (same sample rate)."""
    if not pcm_bytes:
        return b""
    return audioop.lin2ulaw(pcm_bytes, _WIDTH)


# ---------------------------------------------------------------------------
# Stateful resampler (one instance per stream + direction)
# ---------------------------------------------------------------------------


class Resampler:
    """Carries ``audioop.ratecv`` state across chunks for one direction.

    Create one for the inbound leg (8 kHz -> 16 kHz, for STT) and one for the
    outbound leg (24 kHz -> 8 kHz, for Twilio). Reusing a single instance for a
    whole call eliminates the per-chunk click that a stateless resample causes.
    """

    def __init__(self, from_rate: int, to_rate: int, *, channels: int = 1) -> None:
        self.from_rate = from_rate
        self.to_rate = to_rate
        self.channels = channels
        # Opaque ``audioop.ratecv`` filter state; carried back into ratecv as-is.
        self._state: Any = None

    def process(self, pcm16: bytes) -> bytes:
        """Resample one chunk of int16 PCM, carrying filter state forward."""
        if not pcm16:
            return b""
        if self.from_rate == self.to_rate:
            return pcm16
        converted, self._state = audioop.ratecv(
            pcm16, _WIDTH, self.channels, self.from_rate, self.to_rate, self._state
        )
        return converted

    def reset(self) -> None:
        """Drop accumulated filter state (e.g. after a barge-in flush)."""
        self._state = None


def resample_pcm16(pcm16: bytes, from_rate: int, to_rate: int) -> bytes:
    """Stateless one-shot resample. Use ``Resampler`` for streaming legs."""
    return Resampler(from_rate, to_rate).process(pcm16)


# ---------------------------------------------------------------------------
# Convenience: end-to-end transcodes
# ---------------------------------------------------------------------------


def twilio_ulaw_to_stt_pcm(ulaw_bytes: bytes, resampler: Resampler | None = None) -> bytes:
    """Twilio mu-law 8 kHz -> int16 16 kHz PCM, ready for ``transcribe_pcm``."""
    pcm8 = ulaw_to_pcm16(ulaw_bytes)
    if resampler is not None:
        return resampler.process(pcm8)
    return resample_pcm16(pcm8, TWILIO_SAMPLE_RATE, STT_SAMPLE_RATE)


def tts_pcm_to_twilio_ulaw(
    pcm16: bytes, source_rate: int = TTS_SAMPLE_RATE, resampler: Resampler | None = None
) -> bytes:
    """TTS int16 PCM (24 kHz default) -> Twilio mu-law 8 kHz."""
    if resampler is not None:
        pcm8 = resampler.process(pcm16)
    else:
        pcm8 = resample_pcm16(pcm16, source_rate, TWILIO_SAMPLE_RATE)
    return pcm16_to_ulaw(pcm8)


# ---------------------------------------------------------------------------
# 20 ms framing
# ---------------------------------------------------------------------------


def frame_ulaw(ulaw_bytes: bytes, frame_bytes: int = TWILIO_FRAME_BYTES) -> list[bytes]:
    """Split a mu-law buffer into fixed-size frames.

    The final partial frame (if any) is zero-padded with mu-law silence
    (``0xFF`` is the mu-law code for digital zero) so every frame Twilio
    receives is exactly ``frame_bytes`` long.
    """
    if not ulaw_bytes:
        return []
    frames: list[bytes] = []
    for offset in range(0, len(ulaw_bytes), frame_bytes):
        chunk = ulaw_bytes[offset : offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk + b"\xff" * (frame_bytes - len(chunk))
        frames.append(chunk)
    return frames


# ---------------------------------------------------------------------------
# Lightweight energy + silence-timeout endpointer (headless-safe default)
# ---------------------------------------------------------------------------


@dataclass
class _EndpointerState:
    speaking: bool = False
    silence_ms: int = 0
    speech_ms: int = 0
    buffer: list[bytes] = field(default_factory=list)


class EnergyEndpointer:
    """Detects end-of-turn from frame RMS energy + a trailing-silence timer.

    Deliberately dependency-free (no Silero/PyTorch) so the telephony path
    runs on a 1 vCPU / 1 GB VPS with no model download (cloud-first doctrine).
    The mic path keeps Silero; for 8 kHz phone audio a simple energy gate is
    adequate and fully deterministic for tests.

    Feed it inbound int16 PCM frames (any rate; ``frame_ms`` describes their
    duration). It accumulates audio while the caller speaks and, once
    ``silence_ms`` of trailing quiet follows real speech, returns the buffered
    utterance as one int16 PCM blob. Returns ``None`` between endpoints.
    """

    def __init__(
        self,
        *,
        sample_rate: int = STT_SAMPLE_RATE,
        rms_threshold: int = 500,
        silence_ms: int = 700,
        min_speech_ms: int = 250,
        max_utterance_ms: int = 15_000,
    ) -> None:
        self.sample_rate = sample_rate
        self.rms_threshold = rms_threshold
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_ms = max_utterance_ms
        self._st = _EndpointerState()

    @staticmethod
    def _rms(pcm16: bytes) -> int:
        if not pcm16:
            return 0
        try:
            return audioop.rms(pcm16, _WIDTH)
        except audioop.error:  # pragma: no cover - malformed frame
            return 0

    def _frame_ms(self, pcm16: bytes) -> int:
        samples = len(pcm16) // _WIDTH
        if self.sample_rate <= 0:
            return 0
        return int(samples * 1000 / self.sample_rate)

    def push(self, pcm16: bytes) -> bytes | None:
        """Push one PCM frame; return a finished utterance or ``None``."""
        if not pcm16:
            return None
        dur = self._frame_ms(pcm16)
        is_speech = self._rms(pcm16) >= self.rms_threshold
        st = self._st

        if is_speech:
            if not st.speaking:
                st.speaking = True
            st.buffer.append(pcm16)
            st.speech_ms += dur
            st.silence_ms = 0
        elif st.speaking:
            # Keep the trailing-silence audio in the buffer (natural tail).
            st.buffer.append(pcm16)
            st.silence_ms += dur

        total_ms = st.speech_ms + st.silence_ms
        ended = st.speaking and (
            (st.silence_ms >= self.silence_ms and st.speech_ms >= self.min_speech_ms)
            or total_ms >= self.max_utterance_ms
        )
        if ended:
            utterance = b"".join(st.buffer)
            had_enough = st.speech_ms >= self.min_speech_ms
            self._st = _EndpointerState()
            return utterance if had_enough else None
        return None

    def flush(self) -> bytes | None:
        """Return any buffered speech regardless of trailing silence."""
        st = self._st
        if st.speaking and st.speech_ms >= self.min_speech_ms:
            utterance = b"".join(st.buffer)
            self._st = _EndpointerState()
            return utterance
        self._st = _EndpointerState()
        return None

    def reset(self) -> None:
        """Discard the current utterance buffer (e.g. on barge-in)."""
        self._st = _EndpointerState()


__all__ = [
    "EnergyEndpointer",
    "Resampler",
    "STT_SAMPLE_RATE",
    "TTS_SAMPLE_RATE",
    "TWILIO_FRAME_BYTES",
    "TWILIO_FRAME_MS",
    "TWILIO_SAMPLE_RATE",
    "frame_ulaw",
    "pcm16_to_ulaw",
    "resample_pcm16",
    "tts_pcm_to_twilio_ulaw",
    "twilio_ulaw_to_stt_pcm",
    "ulaw_to_pcm16",
]
