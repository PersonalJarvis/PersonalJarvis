"""Judging a wake-word recording — the clipping detector above all.

Clipping is the most common reason a custom wake word will not train, so a
detector that misses it, or one that cries wolf on clean audio, is worse than
none: it either lets the bad samples through or teaches people to ignore it.
Both directions are asserted here.

Pure arithmetic over int16 arrays — no device, no files, so this runs on a
headless CI box exactly as it does on a laptop.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio.wake_sample_quality import (
    CLIP_RATIO_LIMIT,
    CLIP_RUN_LIMIT,
    LEVEL_TOO_QUIET_DBFS,
    frame_level,
    judge_sample,
    summarize_set,
)

SR = 16_000
FULL = 32767


def tone(amplitude: float, seconds: float = 2.0) -> np.ndarray:
    """A sine at a given fraction of full scale — clean audio by construction."""
    t = np.arange(int(SR * seconds)) / SR
    return (np.sin(2 * np.pi * 220 * t) * amplitude * FULL).astype(np.int16)


def clipped_tone(seconds: float = 2.0, overdrive: float = 1.6) -> np.ndarray:
    """A sine driven past full scale and cut flat — what real clipping is."""
    t = np.arange(int(SR * seconds)) / SR
    raw = np.sin(2 * np.pi * 220 * t) * overdrive * FULL
    return np.clip(raw, -FULL, FULL).astype(np.int16)


# ---------------------------------------------------------------- clipping


def test_a_clipped_recording_is_detected():
    quality = judge_sample(clipped_tone())
    assert quality.clipped
    assert quality.verdict == "clipped"
    assert not quality.usable
    assert "loud" in quality.advice.lower()


def test_clean_loud_audio_is_not_called_clipped():
    # 0.9 of full scale is loud and completely undistorted. Flagging this would
    # train people to ignore the warning.
    quality = judge_sample(tone(0.9))
    assert not quality.clipped
    assert quality.usable


def test_a_single_peaked_sample_is_not_clipping():
    pcm = tone(0.5)
    pcm[1000] = FULL
    assert not judge_sample(pcm).clipped


def test_a_short_flat_top_is_clipping():
    pcm = tone(0.5)
    pcm[1000 : 1000 + CLIP_RUN_LIMIT] = FULL
    quality = judge_sample(pcm)
    assert quality.clipped
    assert quality.longest_run >= CLIP_RUN_LIMIT


def test_a_run_one_sample_short_of_the_limit_is_not_clipping():
    pcm = tone(0.5)
    pcm[1000 : 1000 + CLIP_RUN_LIMIT - 1] = FULL
    assert not judge_sample(pcm).clipped


def test_a_hot_mic_is_caught_by_the_ratio_even_without_a_long_run():
    # Scattered single peaks: no run reaches the limit, but the proportion of
    # pinned samples says the input gain is too high throughout.
    pcm = tone(0.5)
    count = int(pcm.size * CLIP_RATIO_LIMIT * 4)
    pcm[np.linspace(0, pcm.size - 1, count, dtype=int)] = FULL
    quality = judge_sample(pcm)
    assert quality.longest_run < CLIP_RUN_LIMIT
    assert quality.clipped_ratio > CLIP_RATIO_LIMIT
    assert quality.clipped


def test_negative_full_scale_counts_as_clipping_too():
    # Cutting only the trough is still a flat top; a magnitude-blind detector
    # would pass a recording that is audibly distorted.
    pcm = tone(0.5)
    pcm[500 : 500 + CLIP_RUN_LIMIT] = -FULL
    assert judge_sample(pcm).clipped


# ---------------------------------------------------------------- level


def test_a_quiet_recording_is_flagged_but_ranked_below_clipping():
    quality = judge_sample(tone(0.002))
    assert quality.too_quiet
    assert quality.verdict == "quiet"
    assert quality.rms_dbfs < LEVEL_TOO_QUIET_DBFS
    assert "quiet" in quality.advice.lower()


def test_clipping_outranks_quietness_in_the_verdict():
    # A recording can be both mostly quiet and clipped on one syllable. The
    # verdict must name the clipping: a quiet take still carries the phrase, a
    # clipped one carries a different sound.
    pcm = tone(0.001)
    pcm[100 : 100 + CLIP_RUN_LIMIT] = FULL
    quality = judge_sample(pcm)
    assert quality.clipped
    assert quality.verdict == "clipped"


def test_digital_silence_reports_a_finite_level_rather_than_minus_infinity():
    quality = judge_sample(np.zeros(SR, dtype=np.int16))
    assert np.isfinite(quality.rms_dbfs)
    assert quality.too_quiet


def test_an_empty_recording_does_not_raise():
    quality = judge_sample(np.zeros(0, dtype=np.int16))
    assert not quality.usable
    assert np.isfinite(quality.rms_dbfs)


def test_a_well_levelled_sample_reads_as_good():
    quality = judge_sample(tone(0.15))
    assert quality.verdict == "good"
    assert quality.usable
    assert quality.advice == ""


# ---------------------------------------------------------------- live meter


def test_the_live_meter_flags_a_clipping_block_while_it_is_happening():
    block = clipped_tone(seconds=0.05)
    assert frame_level(block).clipping


def test_the_live_meter_does_not_flag_a_clean_block():
    assert not frame_level(tone(0.6, seconds=0.05)).clipping


def test_the_meter_bar_stays_inside_its_range():
    for amplitude in (0.0001, 0.01, 0.5, 1.0):
        fraction = frame_level(tone(amplitude, seconds=0.05)).meter_fraction
        assert 0.0 <= fraction <= 1.0


def test_the_meter_bar_grows_with_level():
    quiet = frame_level(tone(0.01, seconds=0.05)).meter_fraction
    loud = frame_level(tone(0.8, seconds=0.05)).meter_fraction
    assert loud > quiet


def test_an_empty_block_is_silence_not_a_crash():
    level = frame_level(np.zeros(0, dtype=np.int16))
    assert not level.clipping
    assert level.meter_fraction == pytest.approx(0.0)


# ---------------------------------------------------------------- the set


def test_the_summary_counts_each_bad_sample_once():
    both = judge_sample(_quiet_and_clipped())
    summary = summarize_set([judge_sample(tone(0.15)), both], needed=2)
    assert summary.total == 2
    assert summary.usable == 1
    assert summary.clipped == 1
    # Counted as clipped, not also as quiet — otherwise the columns overstate
    # how many recordings are actually unusable.
    assert summary.quiet == 0


def test_the_summary_says_how_many_more_are_needed():
    summary = summarize_set([judge_sample(tone(0.15))], needed=15)
    assert not summary.enough
    assert "14 more" in summary.headline


def test_a_full_set_says_it_is_enough():
    summary = summarize_set([judge_sample(tone(0.15))] * 15, needed=15)
    assert summary.enough
    assert "enough" in summary.headline


def _quiet_and_clipped() -> np.ndarray:
    pcm = tone(0.001)
    pcm[100 : 100 + CLIP_RUN_LIMIT] = FULL
    return pcm
