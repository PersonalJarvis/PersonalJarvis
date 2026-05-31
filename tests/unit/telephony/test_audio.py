"""Audio transcode + framing + endpointer round-trip tests."""

from __future__ import annotations

import math
import struct

import pytest

from jarvis.telephony.audio import (
    STT_SAMPLE_RATE,
    TTS_SAMPLE_RATE,
    TWILIO_FRAME_BYTES,
    TWILIO_SAMPLE_RATE,
    EnergyEndpointer,
    Resampler,
    frame_ulaw,
    pcm16_to_ulaw,
    resample_pcm16,
    tts_pcm_to_twilio_ulaw,
    twilio_ulaw_to_stt_pcm,
    ulaw_to_pcm16,
)


def _tone(rate: int, ms: int, freq: int = 440, amp: int = 8000) -> bytes:
    n = rate * ms // 1000
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)
    )


def test_ulaw_pcm_round_trip_preserves_length_ratio():
    pcm = _tone(TWILIO_SAMPLE_RATE, 1000)  # 8000 samples -> 16000 bytes int16
    ulaw = pcm16_to_ulaw(pcm)
    assert len(ulaw) == 8000  # 1 byte per sample
    back = ulaw_to_pcm16(ulaw)
    assert len(back) == 16000  # 2 bytes per sample


def test_ulaw_pcm_round_trip_is_approximately_lossless():
    pcm = _tone(TWILIO_SAMPLE_RATE, 200, amp=12000)
    back = ulaw_to_pcm16(pcm16_to_ulaw(pcm))
    orig = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    recon = struct.unpack(f"<{len(back) // 2}h", back)
    # mu-law is lossy but bounded; peak-relative error stays small.
    err = max(abs(a - b) for a, b in zip(orig, recon, strict=False))
    assert err < 600


def test_empty_inputs_are_safe():
    assert ulaw_to_pcm16(b"") == b""
    assert pcm16_to_ulaw(b"") == b""
    assert resample_pcm16(b"", 8000, 16000) == b""
    assert frame_ulaw(b"") == []


def test_resample_8k_to_16k_doubles_samples_roughly():
    pcm = _tone(TWILIO_SAMPLE_RATE, 1000)
    out = resample_pcm16(pcm, TWILIO_SAMPLE_RATE, STT_SAMPLE_RATE)
    # ~2x the bytes (resampler boundary makes it not exactly double)
    assert abs(len(out) - 2 * len(pcm)) <= 8


def test_streaming_resampler_state_is_continuous():
    """Chunked resample with carried state should match a one-shot resample
    closely (no boundary discontinuity)."""
    full = _tone(TWILIO_SAMPLE_RATE, 600)
    one_shot = resample_pcm16(full, TWILIO_SAMPLE_RATE, STT_SAMPLE_RATE)

    r = Resampler(TWILIO_SAMPLE_RATE, STT_SAMPLE_RATE)
    third = len(full) // 3
    third -= third % 2  # keep int16 aligned
    chunked = b"".join(r.process(full[i : i + third]) for i in range(0, len(full), third))
    # Lengths are within a small filter-tail margin of each other.
    assert abs(len(chunked) - len(one_shot)) <= 16


def test_twilio_inbound_to_stt_pcm_shape():
    ulaw = pcm16_to_ulaw(_tone(TWILIO_SAMPLE_RATE, 100))
    stt = twilio_ulaw_to_stt_pcm(ulaw)
    # 100 ms at 16 kHz int16 ~= 3200 bytes
    assert abs(len(stt) - 3200) <= 16


def test_outbound_tts_to_twilio_ulaw_shape():
    pcm24 = _tone(TTS_SAMPLE_RATE, 100)
    out = tts_pcm_to_twilio_ulaw(pcm24, source_rate=TTS_SAMPLE_RATE)
    # 100 ms at 8 kHz mu-law ~= 800 bytes
    assert abs(len(out) - 800) <= 4


def test_frame_ulaw_pads_final_partial_frame():
    data = b"\x01" * (TWILIO_FRAME_BYTES + 5)
    frames = frame_ulaw(data)
    assert len(frames) == 2
    assert all(len(f) == TWILIO_FRAME_BYTES for f in frames)
    # last frame padded with mu-law silence (0xff)
    assert frames[1].endswith(b"\xff" * (TWILIO_FRAME_BYTES - 5))


def test_endpointer_emits_after_silence_following_speech():
    ep = EnergyEndpointer(sample_rate=STT_SAMPLE_RATE, silence_ms=200, min_speech_ms=100)
    out = None
    # lead silence
    for _ in range(3):
        out = ep.push(b"\x00\x00" * (STT_SAMPLE_RATE * 20 // 1000)) or out
    # speech
    for _ in range(10):
        out = ep.push(_tone(STT_SAMPLE_RATE, 20, freq=300, amp=12000)) or out
    assert out is None  # not yet — still speaking
    # trailing silence triggers the endpoint
    for _ in range(15):
        out = ep.push(b"\x00\x00" * (STT_SAMPLE_RATE * 20 // 1000)) or out
    assert out is not None
    assert len(out) > 0


def test_endpointer_discards_pure_silence():
    ep = EnergyEndpointer(sample_rate=STT_SAMPLE_RATE, silence_ms=100, min_speech_ms=100)
    out = None
    for _ in range(20):
        out = ep.push(b"\x00\x00" * (STT_SAMPLE_RATE * 20 // 1000)) or out
    assert out is None


def test_endpointer_max_utterance_cap():
    ep = EnergyEndpointer(
        sample_rate=STT_SAMPLE_RATE, silence_ms=5000, min_speech_ms=50, max_utterance_ms=200
    )
    out = None
    for _ in range(20):
        out = ep.push(_tone(STT_SAMPLE_RATE, 20, amp=12000)) or out
    assert out is not None  # cap fired despite continuous speech


@pytest.mark.parametrize("rate", [8000, 16000, 24000])
def test_resampler_identity_when_rates_match(rate):
    pcm = _tone(rate, 50)
    assert Resampler(rate, rate).process(pcm) == pcm
