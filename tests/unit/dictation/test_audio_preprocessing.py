"""Capability-first desktop dictation audio preprocessing."""

import struct

import pytest

from jarvis.dictation.audio_preprocessing import (
    AudioQualityAccumulator,
    assess_desktop_audio_preprocessing,
    prepare_dictation_pcm,
)


def test_unverified_processing_never_changes_the_pcm() -> None:
    pcm = bytes(range(128))
    prepared, decision = prepare_dictation_pcm(
        pcm, echo_cancellation_requested=True
    )

    assert prepared is pcm
    assert decision.pcm_changed is False
    assert decision.audit() == (
        "audio_preprocessing:raw_pcm",
        "noise_suppression:unavailable",
        "automatic_gain_control:unavailable",
        "acoustic_echo_cancellation:unavailable",
    )


def test_disabled_echo_setting_is_reported_as_off() -> None:
    decision = assess_desktop_audio_preprocessing(
        echo_cancellation_requested=False
    )

    assert decision.acoustic_echo_cancellation == "off"
    assert decision.noise_suppression == "unavailable"
    assert decision.automatic_gain_control == "unavailable"


def test_quality_accumulator_measures_rate_rms_and_clipping() -> None:
    meter = AudioQualityAccumulator()
    meter.add_chunk(
        struct.pack("<hhhh", 0, 32_767, -32_768, 16_384),
        sample_rate_hz=16_000,
    )

    quality = meter.snapshot()

    assert quality.sample_rate_hz == 16_000
    assert quality.rms == pytest.approx(0.75, abs=0.02)
    assert quality.clipping_ratio == 0.5
    assert quality.dropout_count == 0


def test_quality_accumulator_detects_timestamp_gaps_and_capture_drops() -> None:
    meter = AudioQualityAccumulator()
    pcm = b"\x00\x01" * 512  # 32 ms at 16 kHz
    meter.add_chunk(pcm, sample_rate_hz=16_000, timestamp_ns=1_000_000_000)
    meter.add_chunk(pcm, sample_rate_hz=16_000, timestamp_ns=1_096_000_000)

    quality = meter.snapshot(reported_dropouts=3)

    assert quality.dropout_count == 3
    assert quality.dropout_duration_ms == 64
