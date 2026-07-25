"""The native-rate fallback resampler must not fold high frequencies into speech.

Where the fallback engages — CoreAudio and ALSA/PipeWire, i.e. macOS and Linux —
interpolating 48 kHz straight down to 16 kHz mirrors everything above the 8 kHz
target Nyquist back into the band the wake models actually read. Windows never
reaches this path (MME resamples host-side), so the whole wake stack was
calibrated on clean captures and deployed on aliased audio: the openWakeWord
melspec, the Vosk MFCCs, the Whisper log-mel, and the AP-27 raw-energy constants
all see noise that is absent from their training data and from the machine the
thresholds were measured on.

These tests pin the properties, not an implementation: speech survives, an
out-of-band tone does not come back as an in-band ghost, and the filter stays
continuous across PortAudio callback boundaries.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio.capture import _StreamingPcm16Resampler


def _tone(freq_hz: float, rate: int, seconds: float, amplitude: float = 0.5) -> bytes:
    t = np.arange(int(rate * seconds), dtype=np.float64) / rate
    wave = np.sin(2.0 * np.pi * freq_hz * t) * amplitude * 32767.0
    return np.clip(np.rint(wave), -32768, 32767).astype("<i2").tobytes()


def _to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0


def _rms(signal: np.ndarray) -> float:
    return float(np.sqrt(np.mean(signal**2))) if signal.size else 0.0


# A 0.5-amplitude sine has this RMS; every case below feeds exactly that, so
# output RMS reads directly as attenuation.
_INPUT_RMS = 0.5 / np.sqrt(2.0)


@pytest.mark.parametrize(
    ("freq_hz", "max_out_rms", "why"),
    (
        # Speech band: must pass essentially untouched. Measured -0.1 / -0.0 dB.
        (1_000, None, "core speech"),
        (3_000, None, "consonants / upper formants"),
        # Above the 8 kHz target Nyquist: would fold back into speech.
        # Measured -27.7 dB at 9 kHz, -51.0 dB at 12 kHz, -62.2 dB at 20 kHz.
        (9_000, 0.05, "just above Nyquist — mirrors to 7 kHz"),
        (12_000, 0.01, "mirrors onto 4 kHz, dead centre of the speech band"),
        (20_000, 0.01, "coil / switching-supply whine"),
    ),
)
def test_out_of_band_energy_cannot_fold_into_the_speech_band(
    freq_hz: int, max_out_rms: float | None, why: str
) -> None:
    """Aliased energy is indistinguishable from speech to the detector.

    A 12 kHz component mirrors to exactly 4 kHz at a 16 kHz target — dead centre
    of every wake model's feature range — so without a pre-filter fan whine and
    sibilance arrive as broadband in-band noise that is absent from the models'
    training data AND from the Windows captures the AP-27 energy thresholds were
    calibrated on.
    """
    resampler = _StreamingPcm16Resampler(48_000, 16_000, 1)
    out_rms = _rms(_to_float(resampler.process(_tone(freq_hz, 48_000, 0.5))))

    if max_out_rms is None:
        assert out_rms > 0.9 * _INPUT_RMS, (
            f"{freq_hz} Hz ({why}) lost {1 - out_rms / _INPUT_RMS:.1%} of its "
            "amplitude — the filter is cutting into the speech band"
        )
    else:
        assert out_rms < max_out_rms, (
            f"{freq_hz} Hz ({why}) survived at RMS {out_rms:.4f} and will alias "
            "into the band the wake models read"
        )


def test_the_filter_is_continuous_across_callback_boundaries() -> None:
    """PortAudio delivers independent buffers. If the filter restarted per
    callback, every boundary would carry a startup transient — a click, and a
    dip in the wake window that straddles it. Feeding one signal in chunks must
    match feeding it whole."""
    whole = _tone(1_000, 48_000, 0.3)
    single = _to_float(_StreamingPcm16Resampler(48_000, 16_000, 1).process(whole))

    chunked_resampler = _StreamingPcm16Resampler(48_000, 16_000, 1)
    step = 4_800 * 2  # 100 ms of mono PCM16, one PortAudio callback
    pieces = [
        chunked_resampler.process(whole[i : i + step])
        for i in range(0, len(whole), step)
    ]
    chunked = _to_float(b"".join(pieces))

    n = min(single.size, chunked.size)
    assert n > 0
    # Allow the one-frame interpolation phase difference, not a transient.
    assert float(np.max(np.abs(single[:n] - chunked[:n]))) < 0.05


def test_upsampling_needs_no_prefilter() -> None:
    """Only downsampling can alias. An upsampling resampler must not pay for a
    filter it does not need (nor attenuate anything)."""
    resampler = _StreamingPcm16Resampler(16_000, 48_000, 1)
    assert resampler._taps is None
    out = _to_float(resampler.process(_tone(1_000, 16_000, 0.2)))
    assert float(np.sqrt(np.mean(out**2))) > 0.2


def test_passthrough_rate_is_untouched() -> None:
    """Equal rates short-circuit before any filtering — byte-for-byte identity."""
    pcm = _tone(1_000, 16_000, 0.1)
    assert _StreamingPcm16Resampler(16_000, 16_000, 1).process(pcm) == pcm


@pytest.mark.parametrize("channels", (1, 2))
def test_frame_geometry_survives(channels: int) -> None:
    """Interleaved multi-channel input must stay aligned: a filter applied
    across the interleave instead of per channel would blend the channels."""
    resampler = _StreamingPcm16Resampler(48_000, 16_000, channels)
    mono = np.frombuffer(_tone(1_000, 48_000, 0.2), dtype="<i2")
    interleaved = np.repeat(mono[:, None], channels, axis=1).reshape(-1)
    out = resampler.process(interleaved.astype("<i2").tobytes())
    assert len(out) % (2 * channels) == 0
