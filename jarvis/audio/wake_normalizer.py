"""Shared, level-independent input normalization for every wake path.

Root cause of "the wake word only triggers when I shout": the microphone
capture applies no normalization (``capture.pcm_bytes_to_np`` only scales
int16 -> float), and each wake engine gated detection on ABSOLUTE amplitude with
its own fixed thresholds and its own bolt-on gain stage that sat AFTER that
gate. On a quiet mic (a laptop's built-in mic, a headset with low input gain) a
normal-volume utterance never reached the level the detector expected, so the
gate — not the wake pattern — decided detection, and only a shout crossed it.

``AdaptiveWakeNormalizer`` is the one reusable AGC every wake path can share so
absolute loudness is no longer the gate. Per processed frame:

* it tracks the ambient noise floor with an EMA over *quiet* frames (the same
  idea as :class:`jarvis.audio.mic_level.LevelNormalizer`), so on a quiet mic the
  floor settles low and a genuinely quiet wake rises above it;
* it AMPLIFIES ONLY toward a target peak — an already-loud wake is never turned
  down, so the level band a downstream threshold was calibrated on never
  regresses;
* the gain is derived from a rolling peak over the last ``window_s`` of audio
  (not a single frame) so it stays stable across a wake phrase and the
  intra-phrase envelope the neural model relies on is preserved;
* it is gated by the adaptive floor (times ``speech_margin``): flat digital
  silence and steady sub-floor hiss — which never rise above their own tracked
  floor — are returned UNCHANGED, so the AGC can never manufacture an ambient
  false-fire band (the AGC-level analogue of the BUG-009 guard);
* the gain is capped at ``max_gain_db`` so a near-silent floor can't be blown up
  to full scale.

Pure mechanism — no I/O, no model, no OS-specific code — so it behaves
identically on Windows, Linux and macOS. ``reset()`` clears the rolling envelope
so a stale loud burst cannot suppress the gain on the next quiet utterance.
"""
from __future__ import annotations

from collections import deque

import numpy as np

__all__ = ["AdaptiveWakeNormalizer"]

# openWakeWord's native frame is 1280 samples (80 ms). Used only to size the
# rolling-peak window in frames from ``window_s``; any frame length may be
# passed to ``process``.
_DEFAULT_FRAME_SAMPLES = 1280
_DEFAULT_SAMPLE_RATE = 16_000


class AdaptiveWakeNormalizer:
    """Amplify-only, adaptive-noise-floor, capped streaming AGC for wake audio."""

    def __init__(
        self,
        target_peak_dbfs: float = -3.0,
        max_gain_db: float = 30.0,
        # Starting ambient-floor estimate. On a FRESH normalizer this doubles as
        # the sub-floor guard: a frame below ``floor_start * speech_margin`` is
        # treated as silence and left unchanged, so the first wake in a session
        # cannot false-fire on idle hiss. It then adapts down on a quiet mic.
        floor_start: float = 0.006,
        # The floor never drops below this, so a dead-silent mic cannot drive the
        # speech threshold to zero (which would treat any faint noise as speech).
        floor_min: float = 0.002,
        # A frame counts as speech when its rolling peak exceeds
        # ``noise_floor * speech_margin``. 1.8 keeps a quiet wake (well above the
        # settled ambient floor) in while rejecting hiss that merely equals it.
        speech_margin: float = 1.8,
        window_s: float = 1.5,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        frame_samples: int = _DEFAULT_FRAME_SAMPLES,
    ) -> None:
        self._target_peak = float(10.0 ** (target_peak_dbfs / 20.0))  # -3 dBFS ~= 0.707
        self._max_gain = float(10.0 ** (max_gain_db / 20.0))
        self._floor_start = float(floor_start)
        self._floor_min = float(floor_min)
        self._margin = float(speech_margin)
        frame_s = max(1, int(frame_samples)) / float(sample_rate)
        self._window_frames = max(1, int(round(window_s / frame_s)))
        self._recent_peaks: deque[float] = deque(maxlen=self._window_frames)
        self._floor = self._floor_start
        self._speech_present = False

    # -- introspection (used by callers + tests) ---------------------------
    @property
    def speech_present(self) -> bool:
        """Whether the last processed frame's rolling peak cleared the adaptive
        speech threshold — a level-INDEPENDENT "is someone speaking" signal."""
        return self._speech_present

    @property
    def noise_floor(self) -> float:
        """The current adaptive ambient-floor estimate (for diagnostics/tests)."""
        return self._floor

    def reset(self) -> None:
        """Forget the rolling envelope and re-seed the floor (call on stop/re-arm)."""
        self._recent_peaks.clear()
        self._floor = self._floor_start
        self._speech_present = False

    # -- the mechanism -----------------------------------------------------
    def process(self, frame: np.ndarray) -> np.ndarray:
        """Return ``frame`` (int16) amplified toward the target peak, or unchanged
        when below the adaptive floor / already loud enough. Never mutates input."""
        f32 = np.asarray(frame, dtype=np.float32) / 32768.0
        frame_peak = float(np.max(np.abs(f32))) if f32.size else 0.0

        # Adaptive noise floor: EMA only on quiet frames, so it tracks the ambient
        # baseline between words and does not creep up on the speech itself.
        if frame_peak < self._floor * 1.5:
            self._floor = 0.95 * self._floor + 0.05 * frame_peak
        if self._floor < self._floor_min:
            self._floor = self._floor_min

        self._recent_peaks.append(frame_peak)
        rolling_peak = max(self._recent_peaks) if self._recent_peaks else frame_peak

        speech_threshold = self._floor * self._margin
        if rolling_peak < speech_threshold:
            self._speech_present = False
            return np.asarray(frame, dtype=np.int16)  # silence / sub-floor hiss

        self._speech_present = True
        gain = min(self._target_peak / rolling_peak, self._max_gain)
        if gain <= 1.0:
            return np.asarray(frame, dtype=np.int16)  # already loud — amplify-only
        boosted = np.clip(f32 * gain, -1.0, 1.0)
        return (boosted * 32767.0).astype(np.int16)
