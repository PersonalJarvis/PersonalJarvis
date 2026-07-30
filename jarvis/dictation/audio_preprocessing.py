"""Honest capability verdicts for desktop dictation audio preprocessing.

The capture contract is raw 16 kHz mono PCM from PortAudio. PortAudio and
``sounddevice`` expose stream format and device selection; they do not expose a
portable switch for acoustic echo cancellation, noise suppression, or
automatic gain control. Applying an arbitrary DSP filter merely because a
config switch exists can remove quiet speech and make recognition worse.

Until a backend supplies a processor with measured, platform-specific support,
dictation therefore keeps the PCM byte-identical and records the missing
capabilities in its audit telemetry. This is an explicit degradation on
Windows, macOS, Linux, and headless hosts, not an implied feature.
"""

from __future__ import annotations

import math
import sys
from array import array
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioPreprocessingDecision:
    """What the active desktop capture path can safely do."""

    noise_suppression: str
    automatic_gain_control: str
    acoustic_echo_cancellation: str
    pcm_changed: bool = False

    def audit(self) -> tuple[str, ...]:
        """Stable, transcript-free telemetry fields for one dictation."""
        return (
            "audio_preprocessing:raw_pcm",
            f"noise_suppression:{self.noise_suppression}",
            f"automatic_gain_control:{self.automatic_gain_control}",
            f"acoustic_echo_cancellation:{self.acoustic_echo_cancellation}",
        )


@dataclass(frozen=True, slots=True)
class AudioQualityMetrics:
    """Signal facts captured before any STT provider sees the recording."""

    sample_rate_hz: int = 0
    rms: float = 0.0
    clipping_ratio: float = 0.0
    dropout_count: int = 0
    dropout_duration_ms: int = 0


class AudioQualityAccumulator:
    """Streaming PCM16 quality measurement with timestamp-gap detection.

    Keeping the accumulator beside the preprocessing verdict makes the A/B
    contract explicit: both a future processed arm and today's raw control arm
    are measured using the same signal facts. It consumes each captured chunk
    once, so even an unbounded dictation is not copied or rescanned merely for
    telemetry.
    """

    _CLIP_LEVEL = 32_760

    def __init__(self) -> None:
        self._sample_rate_hz = 0
        self._sample_count = 0
        self._sum_squares = 0
        self._clipped_samples = 0
        self._dropout_count = 0
        self._dropout_duration_ns = 0
        self._previous_timestamp_ns = 0
        self._previous_duration_ns = 0

    def add_chunk(
        self,
        pcm: bytes,
        *,
        sample_rate_hz: int,
        timestamp_ns: int = 0,
    ) -> None:
        """Measure one little-endian mono PCM16 chunk. Invalid tails are ignored."""
        rate = max(0, int(sample_rate_hz or 0))
        usable = len(pcm) - (len(pcm) % 2)
        if usable <= 0 or rate <= 0:
            return

        samples = array("h")
        samples.frombytes(pcm[:usable])
        if sys.byteorder != "little":  # pragma: no cover - uncommon host
            samples.byteswap()
        count = len(samples)
        if not count:
            return

        self._sample_rate_hz = rate
        self._sample_count += count
        self._sum_squares += sum(int(value) * int(value) for value in samples)
        self._clipped_samples += sum(
            1 for value in samples if abs(int(value)) >= self._CLIP_LEVEL
        )

        stamp = max(0, int(timestamp_ns or 0))
        duration_ns = round(count * 1_000_000_000 / rate)
        if stamp and self._previous_timestamp_ns and self._previous_duration_ns:
            interval_ns = stamp - self._previous_timestamp_ns
            missing_ns = interval_ns - self._previous_duration_ns
            # PortAudio callback jitter is ordinary. One missing 32 ms frame is
            # not: a 20 ms / 75% grace distinguishes the two without depending
            # on the configured callback block size.
            grace_ns = max(20_000_000, round(self._previous_duration_ns * 0.75))
            if missing_ns > grace_ns:
                self._dropout_count += 1
                self._dropout_duration_ns += missing_ns
        if stamp:
            self._previous_timestamp_ns = stamp
            self._previous_duration_ns = duration_ns

    def snapshot(self, *, reported_dropouts: int = 0) -> AudioQualityMetrics:
        """Return immutable metrics, including capture-queue drops when exposed."""
        rms = (
            math.sqrt(self._sum_squares / self._sample_count) / 32_768.0
            if self._sample_count
            else 0.0
        )
        clipping = (
            self._clipped_samples / self._sample_count if self._sample_count else 0.0
        )
        return AudioQualityMetrics(
            sample_rate_hz=self._sample_rate_hz,
            rms=rms,
            clipping_ratio=clipping,
            dropout_count=max(self._dropout_count, max(0, int(reported_dropouts or 0))),
            dropout_duration_ms=max(0, round(self._dropout_duration_ns / 1_000_000)),
        )


def assess_desktop_audio_preprocessing(
    *, echo_cancellation_requested: bool
) -> AudioPreprocessingDecision:
    """Return the capability-tested verdict for the current raw capture path.

    No optional native module is imported here: base/headless installs must
    remain importable, and none of the installed capture APIs advertises these
    three processing capabilities. The distinction between ``off`` and
    ``unavailable`` makes the existing echo-cancellation setting observable
    without claiming it changes the samples (AP-31).
    """
    return AudioPreprocessingDecision(
        noise_suppression="unavailable",
        automatic_gain_control="unavailable",
        acoustic_echo_cancellation=(
            "unavailable" if echo_cancellation_requested else "off"
        ),
    )


def prepare_dictation_pcm(
    pcm: bytes, *, echo_cancellation_requested: bool
) -> tuple[bytes, AudioPreprocessingDecision]:
    """Return safe PCM plus its preprocessing decision.

    The byte-for-byte return is intentional. A future processor must replace
    this only behind an actual capability probe and quality regression corpus.
    """
    decision = assess_desktop_audio_preprocessing(
        echo_cancellation_requested=echo_cancellation_requested
    )
    return pcm, decision


__all__ = [
    "AudioPreprocessingDecision",
    "AudioQualityAccumulator",
    "AudioQualityMetrics",
    "assess_desktop_audio_preprocessing",
    "prepare_dictation_pcm",
]
