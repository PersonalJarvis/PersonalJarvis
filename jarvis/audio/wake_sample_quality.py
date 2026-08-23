"""Judging a wake-word recording while it is still being made.

Training a custom wake word needs roughly fifteen clean recordings of the
phrase. Collecting them is easy; knowing whether they were any good is not, and
a bad set produces a wake word that either ignores you or fires at nothing. The
single most common cause is CLIPPING — audio recorded so loud that the waveform
is cut flat at the top — because a clipped sample is not a loud version of your
voice, it is a different sound, and the model learns that instead.

This module is the judging, kept separate from the recorder so it can be tested
without a microphone. Everything here is pure arithmetic over int16 frames: no
device access, no I/O, and therefore identical on every operating system.

## What counts as clipping

Not "a sample touched the maximum". At 16 kHz a single peaked sample happens in
perfectly good audio. What identifies real clipping is a RUN of consecutive
samples pinned at the rail — that flat top is the distortion, and it is what a
listener hears as harshness. So both signals are reported:

* ``clipped_ratio`` — how much of the recording is pinned at all, which catches
  a mic that is hot throughout;
* ``longest_run`` — the longest flat top, which catches a recording that is
  fine except for the one syllable that mattered.

Either one crossing its threshold is enough to warn, because they fail
differently and a set can contain both kinds.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "CLIP_RATIO_LIMIT",
    "CLIP_RUN_LIMIT",
    "LEVEL_TARGET_DBFS",
    "LEVEL_TOO_QUIET_DBFS",
    "FrameLevel",
    "SampleQuality",
    "SetSummary",
    "frame_level",
    "judge_sample",
    "summarize_set",
]

#: int16 full scale. A sample at or beyond this is pinned at the rail.
_FULL_SCALE = 32767

#: A run of this many consecutive pinned samples is a flat top rather than a
#: coincidental peak. Three samples at 16 kHz is ~0.19 ms — short enough to
#: catch one distorted syllable, long enough that clean audio does not trip it.
CLIP_RUN_LIMIT = 3

#: Warn once this fraction of the recording is pinned, even without a long run.
#: 0.1 % of a 2-second sample is ~32 samples, which is already an input gain
#: problem rather than a stray transient.
CLIP_RATIO_LIMIT = 0.001

#: The band a usable sample should land in, RMS in dBFS. Above roughly -12 the
#: headroom is gone and the next louder take will clip; below -40 there is more
#: room noise than voice.
LEVEL_TARGET_DBFS = (-30.0, -12.0)
LEVEL_TOO_QUIET_DBFS = -40.0

#: Floor for the dBFS conversion, so digital silence reports a number instead
#: of -inf and every consumer can format it the same way.
_SILENCE_DBFS = -90.0


def _dbfs(amplitude: float) -> float:
    """Amplitude in 0..1 as dBFS, floored so silence is a finite number."""
    if amplitude <= 0.0:
        return _SILENCE_DBFS
    return max(_SILENCE_DBFS, 20.0 * float(np.log10(amplitude)))


@dataclass(frozen=True, slots=True)
class FrameLevel:
    """One block's worth of level, for the live meter."""

    peak_dbfs: float
    rms_dbfs: float
    #: True when this block alone already shows a flat top. Reported per block
    #: so the recorder can say "too loud" WHILE the person is speaking, which
    #: is the only moment the warning can still change anything.
    clipping: bool

    @property
    def meter_fraction(self) -> float:
        """Peak mapped to 0..1 over the meter's useful range (-60..0 dBFS)."""
        return min(1.0, max(0.0, (self.peak_dbfs + 60.0) / 60.0))


def frame_level(frame: np.ndarray) -> FrameLevel:
    """Level and clipping for one block of int16 samples.

    Cheap enough to run in an audio callback: two reductions and a run scan
    over a block that is normally a few hundred samples.
    """
    if frame.size == 0:
        return FrameLevel(_SILENCE_DBFS, _SILENCE_DBFS, clipping=False)
    samples = frame.reshape(-1).astype(np.int32)
    magnitude = np.abs(samples)
    peak = float(magnitude.max()) / _FULL_SCALE
    rms = float(np.sqrt(np.mean((samples.astype(np.float64) / _FULL_SCALE) ** 2)))
    return FrameLevel(
        peak_dbfs=_dbfs(peak),
        rms_dbfs=_dbfs(rms),
        clipping=_longest_run(magnitude >= _FULL_SCALE) >= CLIP_RUN_LIMIT,
    )


def _longest_run(mask: np.ndarray) -> int:
    """Longest run of True in a boolean array.

    Done with a diff over the padded mask rather than a Python loop: a 2-second
    sample is 32000 values and this runs once per finished recording as well as
    per live block.
    """
    if not mask.any():
        return 0
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return int((edges[1::2] - edges[::2]).max())


@dataclass(frozen=True, slots=True)
class SampleQuality:
    """The verdict on one finished recording."""

    rms_dbfs: float
    peak_dbfs: float
    clipped_ratio: float
    longest_run: int

    @property
    def clipped(self) -> bool:
        return (
            self.longest_run >= CLIP_RUN_LIMIT
            or self.clipped_ratio > CLIP_RATIO_LIMIT
        )

    @property
    def too_quiet(self) -> bool:
        return self.rms_dbfs < LEVEL_TOO_QUIET_DBFS

    @property
    def usable(self) -> bool:
        """Whether this sample should go into the training set as it is."""
        return not self.clipped and not self.too_quiet

    @property
    def verdict(self) -> str:
        """One word for a table column. Clipping outranks quietness: a quiet
        take still carries the phrase, a clipped one carries a different sound.
        """
        if self.clipped:
            return "clipped"
        if self.too_quiet:
            return "quiet"
        if not (LEVEL_TARGET_DBFS[0] <= self.rms_dbfs <= LEVEL_TARGET_DBFS[1]):
            return "ok"
        return "good"

    @property
    def advice(self) -> str:
        """What to change before recording this one again. Empty when nothing."""
        if self.clipped:
            return "Too loud — move back from the mic or lower the input gain."
        if self.too_quiet:
            return "Too quiet — move closer or raise the input gain."
        if self.rms_dbfs > LEVEL_TARGET_DBFS[1]:
            return "Close to clipping; a little more distance would be safer."
        if self.rms_dbfs < LEVEL_TARGET_DBFS[0]:
            return "Usable, but a bit more volume would train better."
        return ""


def judge_sample(pcm: np.ndarray) -> SampleQuality:
    """Judge a complete recording of int16 samples."""
    if pcm.size == 0:
        return SampleQuality(_SILENCE_DBFS, _SILENCE_DBFS, 0.0, 0)
    samples = pcm.reshape(-1).astype(np.int32)
    magnitude = np.abs(samples)
    pinned = magnitude >= _FULL_SCALE
    return SampleQuality(
        rms_dbfs=_dbfs(
            float(np.sqrt(np.mean((samples.astype(np.float64) / _FULL_SCALE) ** 2)))
        ),
        peak_dbfs=_dbfs(float(magnitude.max()) / _FULL_SCALE),
        clipped_ratio=float(pinned.sum()) / float(pinned.size),
        longest_run=_longest_run(pinned),
    )


@dataclass(frozen=True, slots=True)
class SetSummary:
    """How the whole captured set stands."""

    total: int
    usable: int
    clipped: int
    quiet: int
    needed: int

    @property
    def enough(self) -> bool:
        return self.usable >= self.needed

    @property
    def headline(self) -> str:
        if self.enough:
            return f"{self.usable} usable samples — enough to train."
        short = self.needed - self.usable
        return (
            f"{self.usable} usable of {self.total} — {short} more needed. "
            "Re-record the flagged ones rather than starting over."
        )


def summarize_set(qualities: list[SampleQuality], needed: int) -> SetSummary:
    """Count how the set stands against the number of samples training needs."""
    return SetSummary(
        total=len(qualities),
        usable=sum(1 for q in qualities if q.usable),
        clipped=sum(1 for q in qualities if q.clipped),
        # A sample can be both; count it under the one that decides its verdict
        # so the two columns add up to the unusable total rather than double
        # counting.
        quiet=sum(1 for q in qualities if q.too_quiet and not q.clipped),
        needed=needed,
    )
