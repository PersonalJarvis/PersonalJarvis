from __future__ import annotations

import numpy as np
import pytest

from jarvis.realtime.audio import StreamingPcm16Resampler


def _tone(sample_rate: int, duration_s: float = 0.1) -> bytes:
    count = int(sample_rate * duration_s)
    time = np.arange(count, dtype=np.float64) / sample_rate
    return (np.sin(2 * np.pi * 440 * time) * 12_000).astype("<i2").tobytes()


@pytest.mark.parametrize(
    ("source_rate", "target_rate"),
    [(48_000, 16_000), (48_000, 24_000), (16_000, 24_000), (24_000, 16_000)],
)
def test_streaming_resampler_preserves_expected_duration(source_rate, target_rate):
    source = _tone(source_rate)
    resampler = StreamingPcm16Resampler(source_rate, target_rate)
    # Exercise real frame boundaries instead of converting one monolithic blob.
    output = b"".join(
        resampler.process(source[offset : offset + 256])
        for offset in range(0, len(source), 256)
    )
    output_samples = len(output) // 2
    expected_samples = int(0.1 * target_rate)
    assert abs(output_samples - expected_samples) <= 2


def test_identity_conversion_is_byte_exact():
    source = _tone(24_000)
    assert StreamingPcm16Resampler(24_000, 24_000).process(source) == source


def test_rejects_partial_pcm_sample():
    with pytest.raises(ValueError, match="complete 16-bit samples"):
        StreamingPcm16Resampler(16_000, 24_000).process(b"\x01")
